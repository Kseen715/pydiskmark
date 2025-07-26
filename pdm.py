import signal
import threading
import os
import time
import platform
import json
import subprocess
import sys
from pprint import pprint

# For disk detection
if platform.system() == 'Windows':
    import win32api
    import win32file
else:
    import psutil

FIO_CONFIG = 'config/cdm8.fio'

def hash_data(data) -> str:
    """Generate a SHA-256 hash of the given data."""
    import hashlib
    sha256 = hashlib.sha256()
    # convert data to string
    data = str(data)
    sha256.update(data.encode('utf-8'))
    return sha256.hexdigest()[:8]  # Return first 8 characters for brevity


def progress_bar(iteration, total, prefix='', length=40, fill='█', print_end="\r"):
    """Display a progress bar in the console."""
    percent = (iteration / total)
    filled_length = int(length * percent)
    bar = fill * filled_length + '-' * (length - filled_length)
    print(f'\r{prefix} |{bar}| {percent:.1%}', end=print_end)

    # Print new line on completion
    if iteration == total:
        print()


def check_fio_available():
    """Check if fio is available in the system."""
    try:
        subprocess.run(['fio', '--version'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except FileNotFoundError:
        return False


def get_available_disks():
    """Detect all available disks in the system."""
    disks = []

    if platform.system() == 'Windows':
        drives = win32api.GetLogicalDriveStrings().split('\000')[:-1]
        for drive in drives:
            try:
                drive_type = win32file.GetDriveType(drive)
                # Only include fixed drives (3) and removable drives (2)
                if drive_type in (2, 3):
                    drive_info = {
                        'path': drive,
                        'type': 'Fixed' if drive_type == 3 else 'Removable',
                        'name': f"Drive {drive}",
                        'size': get_disk_size(drive)
                    }
                    disks.append(drive_info)
            except:
                pass
    else:
        # For Linux/macOS using psutil
        partitions = psutil.disk_partitions(all=False)
        for p in partitions:
            if p.fstype:  # Skip empty or special filesystems
                try:
                    drive_info = {
                        'path': p.mountpoint,
                        'type': 'Fixed',
                        'name': f"{p.device} ({p.fstype})",
                        'size': get_disk_size(p.mountpoint)
                    }
                    disks.append(drive_info)
                except:
                    pass

    return disks


def get_disk_size(path):
    """Get the total size of a disk in GB."""
    try:
        if platform.system() == 'Windows':
            sectors_per_cluster, bytes_per_sector, free_clusters, total_clusters = win32file.GetDiskFreeSpace(
                path)
            total_size = total_clusters * sectors_per_cluster * bytes_per_sector
            return f"{total_size / (1024**3):.2f} GB"
        else:
            usage = psutil.disk_usage(path)
            return f"{usage.total / (1024**3):.2f} GB"
    except:
        return "Unknown"


def run_fio_test(test_path):
    """Run a disk test using fio with the specified parameters."""
    # Set platform-specific parameters for Windows
    ioengine = "windowsaio" if platform.system() == 'Windows' else "libaio"

    cmd = [
        'fio',
        f'--directory={test_path}',
        f'{FIO_CONFIG}',
        '--output-format=json',
        f'--ioengine={ioengine}',
    ]

    try:
        # Shared flag to control the progress bar thread
        stop_progress = threading.Event()

        def run_progress_bar(total_time, name, stop_event):
            for i in range(total_time):
                if stop_event.is_set():
                    return
                time.sleep(1)
                progress_bar(i, total_time, name)

        # Set up signal handler for Ctrl+C
        def signal_handler(sig, frame):
            print("\nCancelling test...")
            stop_progress.set()  # Signal the thread to stop
            # If subprocess is still running, terminate it
            if 'process' in locals():
                process.terminate()
            sys.exit(0)

        # Register the signal handler
        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal_handler)

        # run a progress bar for 270 seconds in a separate thread
        total_time = 70
        progress_thread = threading.Thread(
            target=run_progress_bar, args=(total_time, "FIO Progress", stop_progress))
        # Make it a daemon thread so it exits when the main thread exits
        progress_thread.daemon = True
        progress_thread.start()

        # Run subprocess with appropriate handling
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate()

        # Stop progress bar
        stop_progress.set()

        if process.returncode != 0:
            print(f"Error running fio: {stderr}")
            return {}

        # Parse JSON output
        fio_output = json.loads(stdout)

        # Save json to ./results/{datetime}_log.json
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        results_dir = os.path.join(os.getcwd(), 'results')
        os.makedirs(results_dir, exist_ok=True)

        # Restore the original signal handler
        signal.signal(signal.SIGINT, original_handler)

        return fio_output

    except Exception as e:
        print(f"Error running fio: {e}")
        return {}


def make_humanreadable_speed(speed_bytes):
    """Convert speed in bytes to a human-readable format (MB)."""
    return f"{speed_bytes / (1024**2):.2f}"

def make_humanreadable_time(time_ns):
    """Convert time in nanoseconds to a human-readable format (us)."""
    return f"{time_ns / 1000:.2f}"


def parse_fio_results(job_results):
    # we need to get all jobs names, speed, iops, and latencies
    if 'jobs' not in job_results:
        print("No jobs found in the fio results.")
        return []
    parsed_results = []
    for job in job_results['jobs']:
        job_name = job['jobname']
        job_speed = make_humanreadable_speed(job['read']['bw_bytes'])
        job_iops = job['read']['iops']
        job_lat = make_humanreadable_time(job['read']['lat_ns']['mean'])

        parsed_results.append({
            'name': job_name,
            'speed_mbs': job_speed,
            'iops': job_iops,
            'latency_us': job_lat
        })
    return parsed_results


def main():
    # Check for fio dependency
    if not check_fio_available():
        print("Error: fio is not installed or not available in PATH.")
        print("Please install fio before using this tool.")
        return

    # Detect available disks
    print("Detecting available disks...")
    available_disks = get_available_disks()

    if not available_disks:
        print("No disks detected. Exiting.")
        return

    # Show available disks to the user
    print("\nAvailable disks:")
    # Print header with appropriate columns
    if platform.system() == 'Windows':
        print(f"{'#':<3} {'Name':<12} {'Type':<12} {'Size':<10}")
        print("-" * 40)
        for i, disk in enumerate(available_disks):
            print(
                f"{i+1:<3} {disk['name']:<12} {disk['type']:<12} {disk['size']:<10}")
    else:
        print(f"{'#':<3} {'Name':<20} {'Path':<20} {'Type':<12} {'Size':<10}")
        print("-" * 70)
        for i, disk in enumerate(available_disks):
            print(
                f"{i+1:<3} {disk['name']:<20} {disk['path']:<20} {disk['type']:<12} {disk['size']:<10}")

    # Ask user to select a disk
    selected = -1
    while selected < 0 or selected >= len(available_disks):
        try:
            selected = int(
                input(f"\nSelect a disk to test (1-{len(available_disks)}): ")) - 1
        except ValueError:
            print("Please enter a valid number.")

    selected_disk = available_disks[selected]
    print(f"\nSelected disk: {selected_disk['name']}")

    # Construct test path (/home/user or C\:\\) to the drive root itself
    test_path = ''
    if platform.system() == 'Windows':
        test_path = f"{selected_disk['path'].split(':')[0]}\\:\\\\"
    else:
        test_path = f"{selected_disk['path']}/"
    # print(test_path)

    try:
        print(
            f"\nStarting FIO Disk Speed Tests on {selected_disk['name']}...\n")
        test_result = run_fio_test(test_path)

    finally:
        try:
            os.makedirs("out", exist_ok=True)
        except Exception as e:
            print(f"Error creating output directory: {e}")
            return

        timestamp = time.strftime("%Y%m%d-%H%M%S")

        try:
            with open(f"out/fio_result_{timestamp}_{hash_data(test_result)}.json", 'w') as f:
                json.dump(test_result, f, indent=4)
        except Exception as e:
            print(f"Error saving test results: {e}")
            return
        
        parsed = parse_fio_results(test_result)

        try:
            with open(f"out/pydiskmark_result_{timestamp}_{hash_data(parsed)}.json", 'w') as f:
                json.dump(parsed, f, indent=4)
        except Exception as e:
            print(f"Error saving parsed results: {e}")
            return
        
        print()
        pprint(parsed)


def spprint_fio_to_cdm8(data_json):
    sb_string = ""
    spl_out = []
    for job in data_json:
        spl = job['name'].split('-')
        spl[0] = spl[0].replace('SEQ', 'Sequential').replace('RND', 'Random')
        # split 1M into [[1, M]
        spl[2] = [int(spl[2][:-1]), spl[2][-1].replace('K', 'KiB').replace('M', 'MiB')]
        spl[3] = int(spl[3].split('Q')[1])
        spl[4] = int(spl[4].split('T')[1])
        spl.append(job['speed_mbs'])
        spl.append(job['iops'])
        spl.append(job['latency_us'])
        spl_out.append(spl)

    sb_string += "--------------------------------------------------------------------------------\n"
    sb_string += "* MB/s = 1,000,000 bytes/s [SATA/600 = 600,000,000 bytes/s]\n"
    sb_string += "* KB = 1000 bytes, KiB = 1024 bytes\n\n"
    sb_string += "[Read]\n"
    for job in spl_out:
        if job[1] == 'R':
            sb_string += f"{job[0]:>10} {job[2][0]:>3} {job[2][1]} (Q= {job[3]:>2}, T= {job[4]}): {job[5]:>8} MB/s [ {round(job[6], 1):>8} IOPS] < {job[7]:>8} us>\n"

    sb_string += "\n[Write]\n"
    for job in spl_out:
        if job[1] == 'W':
            sb_string += f"{job[0]:>10} {job[2][0]:>3} {job[2][1]} (Q= {job[3]:>2}, T= {job[4]}): {job[5]:>8} MB/s [ {round(job[6], 1):>8} IOPS] < {job[7]:>8} us>\n"

    sb_string += "\n" + f"{'Date: ':>9}" + time.strftime("%Y-%m-%d %H:%M:%S") + "\n"
    sb_string += f"{'OS: ':>9}" + platform.system() + " " + platform.release()
    sb_string += " [" + platform.platform() + "]\n"

    return sb_string

if __name__ == '__main__':
    # read json
    with open('out/pydiskmark_result_20250726-203743_f3b69e1f.json', 'r') as f:
        data_json = json.load(f)
    print(spprint_fio_to_cdm8(data_json))
    exit()
    main()
