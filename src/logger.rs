use log::{ Record, Level, Metadata, LevelFilter, SetLoggerError };
use chrono::{ DateTime, Utc };
use colored::Colorize;
pub struct LyssaLogger {
}

impl LyssaLogger {
    #[allow(dead_code)]
    pub fn new() -> Self {
        LyssaLogger {}
    }
    
    #[allow(dead_code)]
    pub fn init(self, level: LevelFilter) -> Result<(), SetLoggerError> {
        log::set_logger(Box::leak(Box::new(self))).map(move |()| log::set_max_level(level))
    }
}

impl log::Log for LyssaLogger {
    fn enabled(&self, metadata: &Metadata) -> bool {
        metadata.level() <= Level::Trace
    }

    fn log(&self, record: &Record) {
        if self.enabled(record.metadata()) {
            let now = DateTime::<Utc>::from(Utc::now());
            let log_message = format!(
                "{} [{}] {}",
                now.to_utc().format("%Y-%m-%d %H:%M:%S.%3f"),
                record.level(),
                record.args()
            );
            let verbose_message = format!(
                "{} [{}] {}:{} - {}",
                now.to_utc().format("%Y-%m-%d %H:%M:%S.%3f"),
                record.level(),
                record.file().unwrap_or("unknown"),
                record.line().unwrap_or(0),
                record.args()
            );
            // Write to console with colors
            match record.level() {
                Level::Error => println!("{}", verbose_message.red()),
                Level::Warn => println!("{}", log_message.yellow()),
                Level::Info => println!("{}", log_message),
                Level::Debug => println!("{}", verbose_message.blue()),
                Level::Trace => println!("{}", verbose_message.purple()),
            }
        }
    }

    fn flush(&self) {}
}
