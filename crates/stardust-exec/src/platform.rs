use std::{
    io,
    process::{Child, Command, ExitStatus},
};

#[cfg(unix)]
pub struct Containment(i32);
#[cfg(unix)]
pub fn spawn(cmd: &mut Command) -> io::Result<(Child, Containment)> {
    use std::os::unix::process::CommandExt;
    let child = cmd.process_group(0).spawn()?;
    let pid = child.id() as i32;
    Ok((child, Containment(pid)))
}
#[cfg(unix)]
impl Containment {
    pub fn terminate(&self, _: &mut Child) -> io::Result<()> {
        // This group belongs exclusively to the command started above.
        if unsafe { libc::kill(-self.0, libc::SIGKILL) } == -1 {
            let e = io::Error::last_os_error();
            if e.raw_os_error() != Some(libc::ESRCH) {
                return Err(e);
            }
        }
        Ok(())
    }
}
#[cfg(unix)]
pub fn exit_code(status: ExitStatus) -> i32 {
    use std::os::unix::process::ExitStatusExt;
    status
        .code()
        .unwrap_or_else(|| -status.signal().unwrap_or(1))
}
#[cfg(not(windows))]
pub fn decode(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

#[cfg(windows)]
mod win {
    use super::*;
    use std::os::windows::{io::AsRawHandle, process::CommandExt};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, HANDLE},
        Globalization::{GetOEMCP, MultiByteToWideChar, MB_ERR_INVALID_CHARS},
        System::{
            JobObjects::{AssignProcessToJobObject, CreateJobObjectW, TerminateJobObject},
            Threading::{CREATE_NO_WINDOW, CREATE_SUSPENDED},
        },
    };
    #[link(name = "ntdll")]
    extern "system" {
        fn NtResumeProcess(handle: HANDLE) -> i32;
    }
    pub struct Containment(HANDLE);
    impl Drop for Containment {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
    pub fn spawn(cmd: &mut Command) -> io::Result<(Child, Containment)> {
        let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if handle.is_null() {
            return Err(io::Error::last_os_error());
        }
        let job = Containment(handle);
        let mut child = cmd
            .creation_flags(CREATE_SUSPENDED | CREATE_NO_WINDOW)
            .spawn()?;
        let process = child.as_raw_handle() as HANDLE;
        if unsafe { AssignProcessToJobObject(job.0, process) } == 0 {
            let e = io::Error::last_os_error();
            let _ = child.kill();
            let _ = child.wait();
            return Err(e);
        }
        if unsafe { NtResumeProcess(process) } < 0 {
            let _ = job.terminate(&mut child);
            let _ = child.wait();
            return Err(io::Error::other("NtResumeProcess failed"));
        }
        Ok((child, job))
    }
    impl Containment {
        pub fn terminate(&self, _: &mut Child) -> io::Result<()> {
            if unsafe { TerminateJobObject(self.0, 124) } == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        }
    }
    fn decode_line(bytes: &[u8]) -> String {
        match std::str::from_utf8(bytes) {
            Ok(text) => return text.to_owned(),
            Err(e) if e.error_len().is_none() => {
                return String::from_utf8_lossy(bytes).into_owned()
            }
            Err(_) => {}
        }
        unsafe {
            let cp = GetOEMCP();
            let n = MultiByteToWideChar(
                cp,
                MB_ERR_INVALID_CHARS,
                bytes.as_ptr(),
                bytes.len() as i32,
                std::ptr::null_mut(),
                0,
            );
            if n > 0 {
                let mut wide = vec![0u16; n as usize];
                if MultiByteToWideChar(
                    cp,
                    MB_ERR_INVALID_CHARS,
                    bytes.as_ptr(),
                    bytes.len() as i32,
                    wide.as_mut_ptr(),
                    n,
                ) == n
                {
                    return String::from_utf16_lossy(&wide);
                }
            }
        }
        String::from_utf8_lossy(bytes).into_owned()
    }
    pub fn decode(bytes: &[u8]) -> String {
        bytes
            .split_inclusive(|b| *b == b'\n')
            .map(decode_line)
            .collect()
    }
}
#[cfg(windows)]
pub use win::{decode, spawn, Containment};
#[cfg(windows)]
pub fn exit_code(status: ExitStatus) -> i32 {
    status.code().unwrap_or(1)
}

#[cfg(test)]
mod tests {
    #[test]
    fn unicode_and_ascii() {
        assert_eq!(super::decode("中文\nhello\n".as_bytes()), "中文\nhello\n");
        assert_eq!(super::decode(b"incomplete \xe4"), "incomplete �");
    }
}
