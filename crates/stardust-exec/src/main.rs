//! Sequential UTF-8 JSONL requests; stdout is exclusively protocol events.
use serde::Deserialize;
use serde_json::{json, Value};
use stardust_exec::platform;
use std::{
    collections::HashMap,
    io::{self, BufRead, Read, Write},
    process::{Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

#[derive(Deserialize)]
struct Request {
    argv: Vec<String>,
    cwd: Option<String>,
    env: Option<HashMap<String, String>>,
    stdin: Option<String>,
    timeout_ms: u64,
}
fn emit(value: Value) -> io::Result<()> {
    emit_to(&mut io::stdout().lock(), &value)
}
fn emit_to(out: &mut impl Write, value: &Value) -> io::Result<()> {
    // Serialize before touching stdout: JSON escaping otherwise issues many tiny
    // writes to the protocol pipe (including for the initial started event).
    let mut line = serde_json::to_vec(value)?;
    line.push(b'\n');
    out.write_all(&line)?;
    out.flush()
}
fn run(r: Request) -> io::Result<()> {
    if r.argv.is_empty() || r.timeout_ms == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "argv and positive timeout_ms required",
        ));
    }
    let start = Instant::now();
    let (reader, writer) = os_pipe::pipe()?;
    let mut cmd = Command::new(&r.argv[0]);
    cmd.args(&r.argv[1..])
        .stdout(writer.try_clone()?)
        .stderr(writer)
        .stdin(if r.stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        });
    if let Some(cwd) = r.cwd {
        cmd.current_dir(cwd);
    }
    if let Some(env) = r.env {
        cmd.env_clear().envs(env);
    }
    let (mut child, containment) = platform::spawn(&mut cmd)?;
    emit(json!({"started_pid": child.id()}))?;
    drop(cmd); // close the parent's copies of pipe writers
    if let Some(data) = r.stdin {
        let mut input = child.stdin.take().unwrap();
        thread::spawn(move || {
            let _ = input.write_all(data.as_bytes());
        });
    }
    let (tx, rx) = mpsc::sync_channel(16);
    thread::spawn(move || {
        let mut reader = reader;
        let mut buf = [0; 16384];
        loop {
            match reader.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    if tx.send(buf[..n].to_vec()).is_err() {
                        break;
                    }
                }
            }
        }
    });
    let mut pending = Vec::new();
    let mut status = None;
    let mut exit_at = None;
    let mut timed_out = false;
    loop {
        if status.is_none() {
            status = child.try_wait()?;
            if status.is_some() {
                exit_at = Some(Instant::now());
            }
        }
        if start.elapsed() >= Duration::from_millis(r.timeout_ms) && status.is_none() {
            containment.terminate(&mut child)?;
            status = Some(child.wait()?);
            exit_at = Some(Instant::now());
            timed_out = true;
        }
        match rx.recv_timeout(Duration::from_millis(5)) {
            Ok(bytes) => {
                if status.is_some() {
                    exit_at = Some(Instant::now());
                }
                pending.extend(bytes);
                let cut = pending
                    .iter()
                    .rposition(|b| *b == b'\n')
                    .map(|n| n + 1)
                    .unwrap_or(0);
                if cut > 0 {
                    emit(json!({"output": platform::decode(&pending[..cut])}))?;
                    pending.drain(..cut);
                }
                // Flush long lines only at a valid UTF-8 boundary; retain partial sequences.
                if pending.len() >= 65536 {
                    let cut = match std::str::from_utf8(&pending) {
                        Ok(_) => pending.len(),
                        Err(e) if e.error_len().is_none() => e.valid_up_to(),
                        Err(_) => pending.len(),
                    };
                    if cut > 0 {
                        emit(json!({"output": platform::decode(&pending[..cut])}))?;
                        pending.drain(..cut);
                    }
                }
            }
            Err(mpsc::RecvTimeoutError::Disconnected) if status.is_some() => break,
            Err(_) => {}
        }
        // Detached descendants may retain stdout. Never wait for their lifetime.
        if exit_at.is_some_and(|t| t.elapsed() >= Duration::from_millis(300)) {
            break;
        }
    }
    if !pending.is_empty() {
        emit(json!({"output": platform::decode(&pending)}))?;
    }
    let code = if timed_out {
        124
    } else {
        platform::exit_code(status.unwrap())
    };
    emit(
        json!({"exit_code": code, "elapsed_ms": start.elapsed().as_secs_f64()*1000.0, "timed_out": timed_out}),
    )
}
fn main() -> io::Result<()> {
    for line in io::stdin().lock().lines() {
        let line = line?;
        let result = serde_json::from_str::<Request>(&line)
            .map_err(io::Error::other)
            .and_then(run);
        if let Err(e) = result {
            emit(json!({"error": e.to_string(), "exit_code": 1, "elapsed_ms": 0.0}))?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn protocol_event_uses_one_write_and_flush() {
        #[derive(Default)]
        struct Sink {
            bytes: Vec<u8>,
            writes: usize,
            flushes: usize,
        }
        impl Write for Sink {
            fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
                self.writes += 1;
                self.bytes.extend_from_slice(bytes);
                Ok(bytes.len())
            }
            fn flush(&mut self) -> io::Result<()> {
                self.flushes += 1;
                Ok(())
            }
        }
        let value = json!({"output": "中文\n\"quoted\"".repeat(1000)});
        let mut sink = Sink::default();
        emit_to(&mut sink, &value).unwrap();
        assert_eq!(serde_json::from_slice::<Value>(&sink.bytes).unwrap(), value);
        assert_eq!(sink.bytes.last(), Some(&b'\n'));
        assert_eq!(sink.flushes, 1);
        assert_eq!(
            sink.writes, 1,
            "serialize in memory before writing the protocol event"
        );
    }
}
