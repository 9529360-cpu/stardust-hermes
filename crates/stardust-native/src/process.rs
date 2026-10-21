use pyo3::{prelude::*, types::PyBytes};
use stardust_exec::platform;
use std::{
    collections::HashMap,
    io::{self, Read, Write},
    process::{Child, Command, Stdio},
    sync::Mutex,
};

// Containment owns a Windows HANDLE, accessed only under the mutex. HANDLEs
// are process-wide resources and can be transferred between worker threads.
struct State {
    child: Child,
    containment: platform::Containment,
}
#[cfg(windows)]
unsafe impl Send for State {}

#[pyclass]
pub struct NativeChild {
    state: Mutex<State>,
    reader: Mutex<os_pipe::PipeReader>,
    #[pyo3(get)]
    pid: u32,
}

#[cfg(windows)]
fn readable(reader: &os_pipe::PipeReader) -> io::Result<bool> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::Pipes::PeekNamedPipe;
    let mut available = 0;
    let ok = unsafe {
        PeekNamedPipe(
            reader.as_raw_handle(),
            std::ptr::null_mut(),
            0,
            std::ptr::null_mut(),
            &mut available,
            std::ptr::null_mut(),
        )
    };
    if ok == 0 {
        let err = io::Error::last_os_error();
        if err.raw_os_error() == Some(109) {
            return Ok(true);
        }
        return Err(err);
    }
    if available == 0 {
        std::thread::sleep(std::time::Duration::from_millis(2));
    }
    Ok(available > 0)
}

#[cfg(unix)]
fn readable(reader: &os_pipe::PipeReader) -> io::Result<bool> {
    use std::os::fd::AsRawFd;
    let mut fd = libc::pollfd {
        fd: reader.as_raw_fd(),
        events: libc::POLLIN,
        revents: 0,
    };
    let result = unsafe { libc::poll(&mut fd, 1, 5) };
    if result < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(result > 0)
}

#[pymethods]
impl NativeChild {
    #[new]
    #[pyo3(signature = (argv, cwd, env, stdin))]
    fn new(
        argv: Vec<String>,
        cwd: String,
        env: Option<HashMap<String, String>>,
        stdin: Option<String>,
    ) -> PyResult<Self> {
        if argv.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err("argv required"));
        }
        let (reader, writer) = os_pipe::pipe()?;
        let mut command = Command::new(&argv[0]);
        command
            .args(&argv[1..])
            .current_dir(cwd)
            .stdout(writer.try_clone()?)
            .stderr(writer)
            .stdin(if stdin.is_some() {
                Stdio::piped()
            } else {
                Stdio::null()
            });
        if let Some(env) = env {
            command.env_clear().envs(env);
        }
        let (mut child, containment) = platform::spawn(&mut command)?;
        drop(command);
        if let Some(data) = stdin {
            let mut input = child.stdin.take().unwrap();
            std::thread::spawn(move || {
                let _ = input.write_all(data.as_bytes());
            });
        }
        let pid = child.id();
        Ok(Self {
            state: Mutex::new(State { child, containment }),
            reader: Mutex::new(reader),
            pid,
        })
    }
    fn read<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyBytes>>> {
        let data = py.detach(|| -> io::Result<Option<Vec<u8>>> {
            let mut reader = self.reader.lock().unwrap();
            if !readable(&reader)? {
                return Ok(None);
            }
            let mut buf = vec![0; 16384];
            let n = reader.read(&mut buf)?;
            buf.truncate(n);
            Ok(Some(buf))
        })?;
        Ok(data.map(|bytes| PyBytes::new(py, &bytes)))
    }
    fn poll(&self) -> PyResult<Option<i32>> {
        Ok(self
            .state
            .lock()
            .unwrap()
            .child
            .try_wait()?
            .map(platform::exit_code))
    }
    fn wait(&self, py: Python<'_>) -> PyResult<i32> {
        Ok(py.detach(|| -> io::Result<i32> {
            loop {
                if let Some(status) = self.state.lock().unwrap().child.try_wait()? {
                    return Ok(platform::exit_code(status));
                }
                std::thread::sleep(std::time::Duration::from_millis(5));
            }
        })?)
    }
    fn kill(&self) -> PyResult<()> {
        let mut state = self.state.lock().unwrap();
        let State { child, containment } = &mut *state;
        containment.terminate(child)?;
        Ok(())
    }
}
