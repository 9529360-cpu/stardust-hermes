//! Exercise the real JSONL binary with Rust test executables as child fixtures.
use serde_json::{json, Value};
use stardust_exec::platform::{self, Containment};
use std::{
    io::{BufRead, BufReader, Write},
    process::{Child, Command, Stdio},
    sync::mpsc::{self, Receiver},
    thread,
    time::{Duration, Instant},
};

struct Helper {
    child: Child,
    containment: Containment,
    events: Receiver<Value>,
    command_pid: i32,
}
impl Helper {
    fn start(role: &str) -> Self {
        let mut cmd = Command::new(env!("CARGO_BIN_EXE_stardust-exec"));
        cmd.stdin(Stdio::piped()).stdout(Stdio::piped());
        let (mut child, containment) = platform::spawn(&mut cmd).unwrap();
        let request = json!({
            "argv": [std::env::current_exe().unwrap(), "--ignored", "--exact", "fixture", "--nocapture"],
            "env": std::env::vars().chain([("W17_FIXTURE".into(), role.into())]).collect::<std::collections::HashMap<_, _>>(),
            "timeout_ms": 1000
        });
        writeln!(child.stdin.take().unwrap(), "{request}").unwrap();
        let stdout = child.stdout.take().unwrap();
        let (tx, events) = mpsc::channel();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if tx.send(serde_json::from_str(&line).unwrap()).is_err() {
                    break;
                }
            }
        });
        let started = events.recv_timeout(Duration::from_secs(5)).unwrap();
        Self {
            child,
            containment,
            events,
            command_pid: started["started_pid"].as_i64().unwrap() as i32,
        }
    }
}
impl Drop for Helper {
    fn drop(&mut self) {
        // Own the whole test tree, including the intentionally endless writer.
        // POSIX commands own a separate process group from the helper.
        #[cfg(unix)]
        unsafe {
            libc::kill(-self.command_pid, libc::SIGKILL);
        }
        let _ = self.command_pid;
        let _ = self.containment.terminate(&mut self.child);
        let _ = self.child.wait();
    }
}

#[test]
fn continuous_descendant_cannot_extend_exit_drain() {
    let start = Instant::now();
    let helper = Helper::start("parent");
    let deadline = start + Duration::from_millis(900);
    let mut saw_writer = false;
    loop {
        let event = helper
            .events
            .recv_timeout(deadline.saturating_duration_since(Instant::now()))
            .expect("continuous output must not extend the fixed 300ms post-exit drain");
        saw_writer |= event["output"]
            .as_str()
            .is_some_and(|s| s.contains("writer"));
        if event.get("exit_code").is_some() {
            assert_eq!(event["exit_code"], 0);
            assert_eq!(event["timed_out"], false);
            assert!(saw_writer);
            break;
        }
    }
}

#[test]
fn partial_prompt_flushes_before_exit_and_preserves_split_utf8() {
    let helper = Helper::start("prompt");
    let mut output = String::new();
    let start = Instant::now();
    let mut prompt_at = None;
    loop {
        let event = helper.events.recv_timeout(Duration::from_secs(3)).unwrap();
        if let Some(text) = event["output"].as_str() {
            output.push_str(text);
            if output.contains("prompt: ") && prompt_at.is_none() {
                prompt_at = Some(Instant::now());
                assert!(
                    !output.contains('中'),
                    "flush prompt before the rest of the split character arrives"
                );
            }
            if output.contains("READY\n") && !output.contains("prompt: ") {
                // The fixture flushes READY immediately before the partial prompt.
                assert!(start.elapsed() < Duration::from_secs(1));
                let event = helper
                    .events
                    .recv_timeout(Duration::from_millis(250))
                    .expect("partial prompt must stream on the short flush interval");
                let text = event["output"]
                    .as_str()
                    .expect("output before process exit");
                assert_eq!(text, "prompt: ");
                output.push_str(text);
                prompt_at = Some(Instant::now());
            }
        }
        if event.get("exit_code").is_some() {
            break;
        }
    }
    assert!(prompt_at.is_some());
    assert!(output.contains("prompt: 中"), "{output:?}");
    assert!(
        !output.contains('\u{fffd}'),
        "split UTF-8 must remain intact: {output:?}"
    );
}

#[test]
#[ignore = "subprocess fixture"]
fn fixture() {
    match std::env::var("W17_FIXTURE").unwrap().as_str() {
        "parent" => {
            Command::new(std::env::current_exe().unwrap())
                .args(["--ignored", "--exact", "fixture", "--nocapture"])
                .env("W17_FIXTURE", "writer")
                .stdin(Stdio::null())
                .spawn()
                .unwrap();
            std::process::exit(0);
        }
        "writer" => loop {
            println!("writer");
            std::io::stdout().flush().unwrap();
            thread::sleep(Duration::from_millis(20));
        },
        "prompt" => {
            let mut out = std::io::stdout().lock();
            out.write_all(b"READY\nprompt: \xe4").unwrap();
            out.flush().unwrap();
            thread::sleep(Duration::from_millis(400));
            out.write_all(b"\xb8\xad").unwrap();
            out.flush().unwrap();
            thread::sleep(Duration::from_millis(300));
        }
        other => panic!("unknown fixture: {other}"),
    }
}
