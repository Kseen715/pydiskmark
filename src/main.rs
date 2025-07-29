use clap::Parser;

use serde_json;
mod logger;
use log;
static INIT: std::sync::Once = std::sync::Once::new();

#[derive(Debug, Clone, PartialEq, Eq)]
#[allow(dead_code)]
enum Backend {
    Fio,
    Disktest,
}

#[derive(Parser, Debug)]
#[command(version, about, long_about = None)]
struct Args {
    /// The path to the file to test
    #[arg(short, long, default_value = "none")]
    path: Option<String>,
    /// Log level for the application (e.g., trace, debug, info, warn, error)
    #[arg(long, default_value = "info")]
    log_level: Option<String>,
    /// Backend to use for the application (e.g., "fio", "disktest")
    #[arg(long, default_value = "fio")]
    backend: Option<String>,
}

/// Default bar style for progress bars
///
/// Usage example:
/// ```rust
/// set_progress_style!(bar);
/// set_progress_style!(bar, "[{bar}] {pos}/{len}");
/// set_progress_style!(bar, "[{bar}] {pos}/{len}", "##>");
/// ```
#[allow(unused_macros)]
macro_rules! set_progress_style {
    ($bar:expr) => {
        $bar.set_style(
            ProgressStyle::with_template(
                "[{elapsed_precise}] {bar:40.cyan/blue} {pos:>7}/{len:7} {msg}"
            )
            .unwrap()
            .progress_chars("##-")
        );
    };
    ($bar:expr, $template:expr) => {
        $bar.set_style(
            ProgressStyle::with_template($template)
            .unwrap()
            .progress_chars("##-")
        );
    };
    ($bar:expr, $template:expr, $chars:expr) => {
        $bar.set_style(
            ProgressStyle::with_template($template)
            .unwrap()
            .progress_chars($chars)
        );
    };
}

mod fio {
    use indicatif::{ ProgressBar, ProgressStyle };

    #[allow(dead_code)]
    pub fn is_fio_available() -> bool {
        use std::process::Command;
        let output = Command::new("fio").arg("--version").output();
        match output {
            Ok(_) => true,
            Err(_) => false,
        }
    }

    #[allow(dead_code)]
    pub fn get_fio_version() -> String {
        use std::process::Command;
        let output = Command::new("fio")
            .arg("--version")
            .output()
            .expect("Failed to execute command");
        if !output.status.success() {
            panic!("fio command failed with status: {}", output.status);
        }
        String::from_utf8_lossy(&output.stdout).trim().to_string().replace("fio-", "")
    }

    #[allow(dead_code)]
    pub fn read_config_file(path: &str) -> configparser::ini::Ini {
        use std::fs;
        use std::io::Read;
        use configparser::ini::Ini;
        let mut file = fs::File::open(path).expect(&format!("Failed to open file: {}", path));
        let mut contents = String::new();
        file.read_to_string(&mut contents).expect("Failed to read file");
        let mut config = Ini::new();
        config.read(contents).expect("Failed to parse INI file");
        config
    }

    pub fn read_config_str(config: &str) -> configparser::ini::Ini {
        use configparser::ini::Ini;
        let mut ini = Ini::new();
        ini.read(config.to_string()).expect("Failed to parse INI file");
        ini
    }

    /// Generates FIO job configurations from a given INI configuration.
    /// It parallelizes the sections into the separate job configurations.
    pub fn gen_fio_job_configs(config: &configparser::ini::Ini) -> Vec<configparser::ini::Ini> {
        let map = config.get_map().unwrap();
        let sections = config.sections();
        // println!("{:?}", sections);
        let mut job_configs = Vec::new();
        // iterate over sections
        for (section_name, _) in map.iter() {
            if section_name == "global" {
                continue; // Skip the global section
            }
            let mut current_job = config.clone();
            for key in sections.iter() {
                if key == section_name || key == "global" {
                    continue; // Skip the section name key
                }
                _ = current_job.remove_section(key);
            }
            if section_name != "global" {
                current_job.set(&section_name, "startdelay", Some(std::string::String::from("0")));
            }
            job_configs.push(current_job);
        }
        job_configs
    }

    pub fn run_jobs(jobs: Vec<configparser::ini::Ini>) -> Vec<serde_json::Value> {
        use std::process::Command;
        let mut result = Vec::new();
        let bar = ProgressBar::new(jobs.len() as u64);
        set_progress_style!(bar);
        bar.set_message("Running FIO jobs");
        bar.inc(0);
        for job in jobs {
            let job_str = job.writes();
            let mut file = std::fs::File::create("temp.fio").expect("Failed to create temp file");
            use std::io::Write;
            file.write_all(job_str.as_bytes()).expect("Failed to write to temp file");
            // Execute the fio command with the temp file
            let output = Command::new("fio")
                .arg("--output-format=json")
                .arg("temp.fio")
                .output()
                .expect("Failed to execute fio command");
            if !output.status.success() {
                panic!("fio command failed with status: {}", output.status);
            }
            let output = String::from_utf8_lossy(&output.stdout);
            let output = if output.starts_with('{') {
                output.to_string()
            } else {
                output
                    .lines()
                    .skip_while(|line| !line.starts_with('{'))
                    .collect::<Vec<_>>()
                    .join("\n")
            };
            let output_map: serde_json::Value = serde_json
                ::from_str(&output)
                .expect("Failed to parse JSON output");
            result.push(output_map.clone());
            log::trace!("FIO job executed successfully: {}", output_map);
            bar.inc(1);
            // Sleep for a 5 seconds to avoid overwhelming the system
            std::thread::sleep(std::time::Duration::from_secs(5));
        }
        bar.finish();
        result
    }
}

mod disktest {
    use disktest_lib::{ Disktest, DtStreamType, DisktestQuiet, DisktestFile, gen_seed_string };
    use indicatif::{ ProgressBar, ProgressStyle };

    #[cfg(unix)]
    use std::os::unix::io::{ AsRawFd, FromRawFd };

    use std::fs::File;
    use std::io::{ self, Read, Write };
    use std::thread;
    use std::sync::mpsc;
    use std::path::Path;

    #[cfg(unix)]
    fn capture_disktest_output<F, R>(f: F) -> (String, String, R) where F: FnOnce() -> R {
        // Save original stdout/stderr file descriptors
        let stdout_orig = unsafe { libc::dup(libc::STDOUT_FILENO) };
        let stderr_orig = unsafe { libc::dup(libc::STDERR_FILENO) };
        assert!(stdout_orig != -1 && stderr_orig != -1, "Failed to dup file descriptors");

        // Create pipes for capturing output
        let mut stdout_pipe = [0, 0];
        let mut stderr_pipe = [0, 0];
        unsafe {
            libc::pipe(stdout_pipe.as_mut_ptr());
            libc::pipe(stderr_pipe.as_mut_ptr());
        }

        // Convert pipe ends to File objects
        let mut stdout_reader = unsafe { File::from_raw_fd(stdout_pipe[0]) };
        let stdout_writer = unsafe { File::from_raw_fd(stdout_pipe[1]) };
        let mut stderr_reader = unsafe { File::from_raw_fd(stderr_pipe[0]) };
        let stderr_writer = unsafe { File::from_raw_fd(stderr_pipe[1]) };

        // Redirect stdout/stderr to pipes
        unsafe {
            libc::dup2(stdout_writer.as_raw_fd(), libc::STDOUT_FILENO);
            libc::dup2(stderr_writer.as_raw_fd(), libc::STDERR_FILENO);
        }

        // Channels to collect captured output
        let (stdout_tx, stdout_rx) = mpsc::channel();
        let (stderr_tx, stderr_rx) = mpsc::channel();

        // Thread to capture stdout
        let stdout_handle = thread::spawn(move || {
            let mut buffer = String::new();
            stdout_reader.read_to_string(&mut buffer).unwrap();
            stdout_tx.send(buffer).unwrap();
        });

        // Thread to capture stderr
        let stderr_handle = thread::spawn(move || {
            let mut buffer = String::new();
            stderr_reader.read_to_string(&mut buffer).unwrap();
            stderr_tx.send(buffer).unwrap();
        });

        // Execute the disktest operation
        let result = f();

        // Restore original stdout/stderr
        unsafe {
            libc::dup2(stdout_orig, libc::STDOUT_FILENO);
            libc::dup2(stderr_orig, libc::STDERR_FILENO);
            libc::close(stdout_orig);
            libc::close(stderr_orig);
        }

        // Close pipe writers to signal EOF
        drop(stdout_writer);
        drop(stderr_writer);

        // Collect captured output
        let stdout = stdout_rx.recv().unwrap();
        let stderr = stderr_rx.recv().unwrap();

        // Wait for reader threads to finish
        stdout_handle.join().unwrap();
        stderr_handle.join().unwrap();

        (stdout, stderr, result)
    }

    pub fn run_write(path: &Path) -> u64 {
        // run 1 warmup and 5 tests
        let mut warm = false;
        let mut results = Vec::new();
        let bar = ProgressBar::new(6);
        set_progress_style!(bar);
        bar.set_message("Running Disktest write");
        bar.inc(0);
        for _ in 0..6 {
            let file = DisktestFile::open(path, true, true).unwrap();
            let mut disktest = Disktest::new(
                DtStreamType::Crc,
                gen_seed_string(8).as_bytes(),
                0,
                false,
                0,
                DisktestQuiet::Normal,
                None
            );

            // Capture stdout/stderr during disktest.write execution
            #[cfg(unix)]
            {
                let (stdout, stderr, result) = capture_disktest_output(|| {
                    match disktest.write(file, 0, 1024 * 1024 * 1024) {
                        Ok(result) => result,
                        Err(_) => 0,
                    }
                });
                log::debug!("Disktest write stdout: {}", stdout);
                log::debug!("Disktest write stderr: {}", stderr);
                bar.inc(1);
                if warm {
                    results.push(result.clone());
                }
            }

            #[cfg(not(unix))]
            {
                let result = match disktest.write(file, 0, 1024 * 1024 * 1024) {
                    Ok(result) => result,
                    Err(_) => 0,
                };
                bar.inc(1);
                if warm {
                    results.push(result.clone());
                }
            }

            warm = true;
            // Sleep for a 5 seconds to avoid overwhelming the system
            std::thread::sleep(std::time::Duration::from_secs(5));
        }
        bar.finish();
        log::debug!("Disktest write results: {:?}", results);
        let result = results.iter().fold(0, |acc, &x| acc + x) / (results.len() as u64);
        result
    }

    pub fn run_verify(path: &Path) -> u64 {
        let mut warm = false;
        let mut results = Vec::new();
        let bar = ProgressBar::new(6);
        set_progress_style!(bar);
        bar.set_message("Running Disktest verify");
        bar.inc(0);
        for _ in 0..6 {
            let file = DisktestFile::open(path, true, true).unwrap();
            let mut disktest = Disktest::new(
                DtStreamType::Crc,
                gen_seed_string(16).as_bytes(),
                0,
                false,
                0,
                DisktestQuiet::NoInfo,
                None
            );
            let result = disktest.verify(file, 0, 1024 * 1024 * 1024).unwrap();
            bar.inc(1);
            if warm {
                results.push(result);
            }
            warm = true;
        }
        bar.finish();
        let result = results.iter().fold(0, |acc, &x| acc + x) / (results.len() as u64);
        result
    }
}

fn main() {
    let args = Args::parse();

    let log_level = match args.log_level.as_deref() {
        Some("trace") => log::LevelFilter::Trace,
        Some("debug") => log::LevelFilter::Debug,
        Some("info") => log::LevelFilter::Info,
        Some("warn") => log::LevelFilter::Warn,
        Some("error") => log::LevelFilter::Error,
        _ => log::LevelFilter::Info, // Default to Info if not specified
    };
    INIT.call_once(|| {
        let _ = logger::LyssaLogger::new().init(log_level);
    });
    log::trace!("Log level set to: {:?}", log_level);

    let backend = match args.backend.as_deref() {
        Some("fio") => Backend::Fio,
        Some("disktest") => Backend::Disktest,
        _ => Backend::Fio, // Default to FIO if not specified
    };
    log::trace!("Backend set to: {:?}", backend);

    match backend {
        Backend::Fio => {
            log::trace!("Testing file: {}", args.path.unwrap());
            let fio_available = fio::is_fio_available();
            log::trace!("FIO available: {}", fio_available);
            if !fio_available {
                log::trace!("FIO is not available on this system");
            } else {
                log::trace!("FIO version: {}", fio::get_fio_version());
            }
            let fio_default_config = include_str!("../config/cdm8.fio");
            let fio_config = fio::read_config_str(fio_default_config);
            log::trace!("FIO config: {:?}", fio_config);

            let fio_result = fio::run_jobs(fio::gen_fio_job_configs(&fio_config));
            log::debug!("{:?}", fio_result);
            log::debug!(
                "{:?}",
                fio_result[0]
                    .get("global options")
                    .unwrap_or(&serde_json::Value::String("unknown".to_string()))
            );
        }
        Backend::Disktest => {
            let write_result = disktest::run_write(args.path.clone().unwrap().as_ref());
            log::debug!("Disktest write result: {}", write_result);
            let verify_result = disktest::run_verify(args.path.clone().unwrap().as_ref());
            log::debug!("Disktest verify result: {}", verify_result);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use log;
    use std::sync::Once;
    static INIT: Once = Once::new();
    use logger::LyssaLogger;

    #[test]
    fn test_test() {
        INIT.call_once(|| {
            let _ = LyssaLogger::new().init(log::LevelFilter::Trace);
        });

        assert!(true);
    }
}
