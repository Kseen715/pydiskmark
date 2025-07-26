import git
import re
import argparse
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

    def get_drive_stats(path) -> (int, int, int):
        """Get the total size of a disk in Bytes."""
        try:
            sectors_per_cluster, bytes_per_sector, free_clusters, total_clusters = win32file.GetDiskFreeSpace(
                path)
            total_size = total_clusters * sectors_per_cluster * bytes_per_sector
            used_size = (total_clusters - free_clusters) * \
                sectors_per_cluster * bytes_per_sector
            return (total_size, used_size, free_clusters * sectors_per_cluster * bytes_per_sector)
        except:
            return "Unknown"
else:
    import psutil

    def get_drive_stats(path) -> (int, int, int):
        """Get the total size of a disk in Bytes."""
        try:
            usage = psutil.disk_usage(path)
            return (usage.total, usage.used, usage.free)
        except:
            return "Unknown"


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

        # Restore the original signal handler
        signal.signal(signal.SIGINT, original_handler)

        # delete fio file if it exists
        try:
            file_path = fio_output["global options"]["directory"] + \
                fio_output["global options"]["filename"]
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Error deleting fio file: {e}")

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


def spprint_fio_to_cdm8(data_json, fio_result=None):
    sb_string = ""

    git_hash = git.Repo(search_parent_directories=True).head.object.hexsha[:7]
    sb_string += f'{f"PDM ({git_hash}): https://github.com/Kseen715/pydiskmark":>80}\n'

    fio_version = fio_result['fio version'] if fio_result and 'fio version' in fio_result else 'Unknown'
    sb_string += f'{f"Flexible I/O Tester ({fio_version}): https://github.com/axboe/fio":>80}\n'

    spl_out = []
    for job in data_json:
        spl = job['name'].split('-')
        spl[0] = spl[0].replace('SEQ', 'Sequential').replace('RND', 'Random')
        # split 1M into [[1, M]
        spl[2] = [int(spl[2][:-1]), spl[2]
                  [-1].replace('K', 'KiB').replace('M', 'MiB')]
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

    sb_string += "\n" + f"{'Test: ':>12}" + fio_result["global options"]['filesize'].replace(
        'g', ' GiB') + " (x" + fio_result["global options"]['loops'] + f") [Measure: {fio_result["global options"]['runtime']} sec]\n"
    sb_string += f"{'Date: ':>12}" + time.strftime("%Y-%m-%d %H:%M:%S") + "\n"

    if platform.system() == 'Windows':
        sb_string += f"{'OS: ':>12}" + platform.system() + " " + \
            platform.release()
    else:
        os_name = platform.freedesktop_os_release(
        )['PRETTY_NAME'] + " " + platform.freedesktop_os_release()['BUILD_ID']
        sb_string += f"{'OS: ':>12}" + os_name + \
            " [" + platform.platform() + "]\n"

    target_max_space, target_used_space, _ = get_drive_stats(
        fio_result["global options"]['directory'])
    sb_string += f"{'Target: ':>12}" + fio_result["global options"]['directory'] + \
        f" {target_used_space/target_max_space:.0%} ({target_used_space/1024**3:.2f}/{target_max_space/1024**3:.2f} GiB)\n"
    sb_string += f"{'Engine: ':>12}" + \
        fio_result["global options"]['ioengine'] + "\n"

    try:
        device_info = pathinfo(fio_result["global options"]['directory'])
        sb_string += f"{'Device: ':>12}" + device_info['device'] + " " + device_info['fstype'] + "\n"
    except Exception as e:
        print(f"Error getting device info: {e}")
        sb_string += f"{'Device: ':>12}unknown\n"
    try:
        i_type, i_gen, i_speed = get_disk_interface(device_info['device'])
        sb_string += f"{'Interface: ':>12}" + i_type + " " + i_gen + " " + i_speed + "\n"
    except Exception as e:
        print(f"Error getting disk interface: {e}")
        sb_string += f"{'Interface: ':>12}unknown\n"

    return sb_string


def disksinfo():
    values = []
    disk_partitions = psutil.disk_partitions(all=False)
    for partition in disk_partitions:
        usage = psutil.disk_usage(partition.mountpoint)
        device = {'device': partition.device,
                  'mountpoint': partition.mountpoint,
                  'fstype': partition.fstype,
                  'opts': partition.opts,
                  'total': usage.total,
                  'used': usage.used,
                  'free': usage.free,
                  'percent': usage.percent
                  }
        values.append(device)
    values = sorted(values, key=lambda device: device['device'])
    return values


def pathinfo(path):
    path = os.path.abspath(path)
    if not path.endswith(os.sep):
        path += os.sep
    if not os.path.exists(path):
        print(f"Error: The specified path '{path}' does not exist.")
        return
    disks = disksinfo()
    for disk in disks:
        if disk['mountpoint'] in path:
            return disk


def get_disk_interface(path):
    device_name = os.path.basename(path)

    base_device = device_name
    partition_file = f'/sys/class/block/{device_name}/partition'
    if os.path.exists(partition_file):
        block_path = f'/sys/class/block/{device_name}'
        if os.path.islink(block_path):
            try:
                real_path = os.path.realpath(block_path)
                parent_dir = os.path.dirname(real_path)
                base_device = os.path.basename(parent_dir)
            except:
                pass
        else:
            base_device = re.sub(r'p?\d+$', '', device_name)

    block_path = f'/sys/class/block/{base_device}'
    if not os.path.exists(block_path):
        block_path = f'/sys/block/{base_device}'
        if not os.path.exists(block_path):
            return ('unknown', None)

    try:
        device_path = os.path.realpath(block_path)
    except:
        return ('unknown', None)

    if base_device.startswith('nvme'):
        interface = 'nvme'
    else:
        if 'usb' in device_path:
            interface = 'usb'
        elif 'ata' in device_path:
            interface = 'sata'
        elif 'nvme' in device_path:
            interface = 'nvme'
        else:
            if 'sas' in device_path:
                interface = 'sas'
            else:
                interface = 'unknown'

    gen = None

    if interface == 'sata':
        try:
            # Resolve the device link to find the physical path
            base_device_name = path.replace('/dev/', '')
            sys_block_device = f'/sys/block/{base_device_name}'
            if not os.path.exists(sys_block_device):
                return None
            device_link = os.readlink(sys_block_device)
            ata_number = device_link.split('/ata')[1].split('/')[0]
            sata_spd_file = f'/sys/class/ata_link/link{ata_number}/sata_spd'
            if os.path.exists(sata_spd_file):
                with open(sata_spd_file, 'r') as f:
                    speed_str = f.read().strip()
                # Map speed string to SATA generation
                if '1.5' in speed_str:
                    gen = '1'
                    speed_str = '150 MB/s'
                elif '3.0' in speed_str:
                    gen = '2'
                    speed_str = '300 MB/s'
                elif '6.0' in speed_str:
                    gen = '3'
                    speed_str = '600 MB/s'
                else:
                    gen = speed_str
            interface = 'SATA'
            return (interface, gen, speed_str)
        except Exception as e:
            pass  # Silently fail and return None

    elif interface == 'nvme':
        parts = device_path.split('/')
        pci_dir = None
        for i, part in enumerate(parts):
            if re.match(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]', part):
                devices_index = parts.index(
                    'devices') if 'devices' in parts else -1
                if devices_index != -1:
                    pci_dir = '/'.join(parts[devices_index:i+1])
                    pci_dir = os.path.join('/sys', pci_dir)
                else:
                    pci_dir = '/sys/devices/' + '/'.join(parts[1:i+1])
                break

        if pci_dir:
            speed_file = os.path.join(pci_dir, 'current_link_speed')
            width_file = os.path.join(pci_dir, 'current_link_width')
            if os.path.exists(speed_file) and os.path.exists(width_file):
                try:
                    with open(speed_file, 'r') as f:
                        speed_str = f.read().strip().split(' PCIe')[0]
                    with open(width_file, 'r') as f:
                        width_str = f.read().strip()
                    if '2.5' in speed_str:
                        gen = f'PCIe Gen1.0x{width_str}'
                    elif '5' in speed_str and ('GT/s' in speed_str or 'G/s' in speed_str):
                        gen = f'PCIe Gen2.0x{width_str}'
                    elif '8' in speed_str and ('GT/s' in speed_str or 'G/s' in speed_str):
                        gen = f'PCIe Gen3.0x{width_str}'
                    elif '16' in speed_str and ('GT/s' in speed_str or 'G/s' in speed_str):
                        gen = f'PCIe Gen4.0x{width_str}'
                    elif '32' in speed_str and ('GT/s' in speed_str or 'G/s' in speed_str):
                        gen = f'PCIe Gen5.0x{width_str}'
                    elif '64' in speed_str and ('GT/s' in speed_str or 'G/s' in speed_str):
                        gen = f'PCIe Gen6.0x{width_str}'
                    elif '128' in speed_str and ('GT/s' in speed_str or 'G/s' in speed_str):
                        gen = f'PCIe Gen7.0x{width_str}'
                    elif '256' in speed_str and ('GT/s' in speed_str or 'G/s' in speed_str):
                        gen = f'PCIe Gen8.0x{width_str}'
                    else:
                        gen = f'{speed_str}x{width_str}'
                    speed_str = f"{float(speed_str.split(' ')[0]) * float(width_str):.0f} GB/s"
                except:
                    pass

    elif interface == 'usb':
        current = device_path
        speed_file = None
        for _ in range(10):
            current = os.path.dirname(current)
            test_file = os.path.join(current, 'speed')
            if os.path.exists(test_file):
                speed_file = test_file
                break

        if speed_file:
            try:
                with open(speed_file, 'r') as f:
                    speed_mbps = f.read().strip()
                try:
                    speed = float(speed_mbps)
                    if speed <= 1.5:
                        gen = '1.0'
                    elif speed <= 12:
                        gen = '1.1'
                    elif speed <= 480:
                        gen = '2.0'
                    elif speed <= 5000:
                        gen = '3.2 Gen1x1'
                    elif speed <= 10000:
                        gen = '3.2 Gen2x1'
                    elif speed <= 20000:
                        gen = '3.2 Gen2x2'
                    elif speed <= 40000:
                        gen = '4.0 Gen3x2'
                    elif speed <= 80000:
                        gen = '4.0 Gen4x2'
                    else:
                        gen = f'{speed_mbps}'
                    speed_str = str(f"{float(speed_mbps) / 8} MB/s")
                except:
                    gen = speed_mbps
            except:
                pass

    if interface == 'nvme':
        interface = 'NVMe'
    elif interface.lower() == 'usb':
        interface = 'USB'
    return (interface, gen, speed_str)


def main():
    # Check for fio dependency
    if not check_fio_available():
        print("Error: fio is not installed or not available in PATH.")
        print("Please install fio before using this tool.")
        return

    parser = argparse.ArgumentParser(
        description='PyDiskMark - A simple disk speed testing tool using fio.')
    parser.add_argument('-p', '--path', type=str,
                        help='Path to the directory to test')
    args = parser.parse_args()

    test_path = ''
    if not args.path:

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
    else:
        test_path = args.path
        # make sure the path is absolute
        test_path = os.path.abspath(test_path)
        # make sure path ends with a slash
        if not test_path.endswith(os.sep):
            test_path += os.sep
        # check if the path exists
        if not os.path.exists(test_path):
            print(f"Error: The specified path '{test_path}' does not exist.")
            return
        print(f"\nUsing custom path: {test_path}")

    test_hash = hash_data({
        'platform': platform.system(),
        'disk_name': selected_disk['name'] if 'selected_disk' in locals() else 'Custom Path',
        'test_path': test_path,
        'date': time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    try:
        print(
            f"\nStarting FIO Disk Speed Tests on {selected_disk['name'] if 'selected_disk' in locals() else test_path}...\n")
        test_result = run_fio_test(test_path)

    finally:
        try:
            os.makedirs("out", exist_ok=True)
        except Exception as e:
            print(f"Error creating output directory: {e}")
            return

        timestamp = time.strftime("%Y%m%d%H%M%S")

        try:
            with open(f"out/fio_result_{timestamp}_{test_hash}.json", 'w') as f:
                json.dump(test_result, f, indent=4)
        except Exception as e:
            print(f"Error saving test results: {e}")
            return

        parsed = parse_fio_results(test_result)

        cdm8_res = spprint_fio_to_cdm8(parsed, test_result)

        try:
            with open(f"out/PDM_{timestamp}_{test_hash}.txt", 'w') as f:
                f.write(cdm8_res)
        except Exception as e:
            print(f"Error saving CDM8 formatted results: {e}")
            return

        print(cdm8_res)


if __name__ == '__main__':
    main()
