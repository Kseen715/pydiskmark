fn main() {
    // println!("cargo:rustc-link-search=native=./src");
    // println!("cargo:rustc-link-search=native=/usr/local/lib");
    // println!("cargo:include=/usr/local/include");
    

    println!("cargo:rustc-attr=allow(non_upper_case_globals)");
    println!("cargo:rustc-attr=allow(non_camel_case_types)");
    println!("cargo:rustc-attr=allow(non_snake_case)");
    println!("cargo:rustc-attr=allow(improper_ctypes)");
}