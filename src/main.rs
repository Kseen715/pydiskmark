use clap::Parser;

use serde_json;
use indicatif;
mod logger;
use log;
static INIT: std::sync::Once = std::sync::Once::new();

#[derive(Parser, Debug)]
#[command(version, about, long_about = None)]
struct Args {
    /// The path to the file to test
    #[arg(short, long, default_value = "none")]
    path: Option<String>,
    /// Log level for the application (e.g., trace, debug, info, warn, error)
    #[arg(long, default_value = "info")]
    log_level: Option<String>,
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
        bar.set_style(
            ProgressStyle::with_template(
                "[{elapsed_precise}] {bar:40.cyan/blue} {pos:>7}/{len:7} {msg}"
            )
                .unwrap()
                .progress_chars("##-")
        );
        bar.set_message("Running FIO jobs");
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
            .get("fio version")
            .unwrap_or(&serde_json::Value::String("unknown".to_string()))
    );
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
