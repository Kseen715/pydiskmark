use clap::Parser;
mod logger;

#[derive(Parser, Debug)]
#[command(version, about, long_about = None)]
struct Args {
    /// The path to the file to test
    #[arg(short, long, default_value = "none")]
    path: String,
}

mod fio {
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

    /// Generates FIO job configurations from a given INI configuration.
    /// It parallelizes the sections into the separate job configurations.
    pub fn gen_fio_job_configs(config: &configparser::ini::Ini) -> Vec<configparser::ini::Ini> {
        let map = config.get_map().unwrap();
        let sections = config.sections();
        println!("{:?}", sections);
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
            job_configs.push(current_job);
        }
        job_configs
    }

    pub fn run_jobs(jobs: Vec<configparser::ini::Ini>) {
        use std::process::Command;
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
            println!(
                "FIO job executed successfully: {}",
                String::from_utf8_lossy(&output.stdout)
            );
            // Sleep for a 5 seconds to avoid overwhelming the system
            std::thread::sleep(std::time::Duration::from_secs(5));
        }
    }
}

fn main() {
    let args = Args::parse();
    // println!("Testing file: {}", args.path);
    // println!("{}", fio::is_fio_available());
    // println!("{}", fio::get_fio_version());
    // println!("{:?}", fio::read_config_file("./config/cdm8.fio").sections());
    println!("{:?}", fio::gen_fio_job_configs(&fio::read_config_file("./config/cdm8.fio")));
    println!("{:?}", fio::gen_fio_job_configs(&fio::read_config_file("./config/cdm8.fio")).len());
    println!("{:?}", fio::run_jobs(fio::gen_fio_job_configs(&fio::read_config_file("./config/cdm8.fio"))));
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
