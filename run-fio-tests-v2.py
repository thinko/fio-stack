import os, sys, stat
from os import path
import subprocess

sys.path.insert(0, path.abspath('../fio-plot'))

import fio_plot
from bench_fio.benchlib import (
    checks,
    display,
    runfio,
    supporting,
    argparsing,
    defaultsettings as defaults,
    parseini,
    network
)

# Define test parameters
run_interactive = False
do_luks_tests = False
test_crypt = ["none", "default", "no-queues", "same-cpu-crypt"]

test_settings = defaults.get_default_settings()

test_settings["type"] = 'device'        # device for block device(s), file for block file
test_settings["destructive"] = True
test_settings["parallel"] = True
test_settings["time_based"] = True
test_settings["mode"] = ["read", "write", "randrw"]
test_settings["block_size"] = ["4k", "16k", "128k", "4M"]
test_settings["iodepth"] = [1, 8, 16]
test_settings["numjobs"] = [1, 3, 8]
#test_settings["rwmixread"] = [50]
test_settings["rwmixread"] = [50, 30, 70]  #TODO: add multiple mixed ratio handling
#test_settings["runtime"] = 60          # default is 60
#test_settings["size"] = None           # default is None
#test_settings["dry_run"] = False       # default is False

output_base = "$HOME/benchmark/fio"
crypt_header_prefix = "/var/tmp/luksheader."
crypt_pass = "correcthorsebatterystaple"

runtime = test_settings["runtime"]
randrw_mix = test_settings["rwmixread"]
iodepths = test_settings["iodepth"]
numjobs = test_settings["numjobs"]
block_size = test_settings["block_size"]
device_prefix = "/dev/"
one_nvme_dev = ["/dev/nvme0n1"]  # Single device
all_nvme_dev = "/dev/nvme0n1:/dev/nvme1n1:/dev/nvme2n1:/dev/nvme3n1:/dev/nvme4n1:/dev/nvme5n1:/dev/nvme6n1:/dev/nvme7n1:/dev/nvme8n1:/dev/nvme9n1"  # 10 devices
all_devs = ["nvme0n1", "nvme1n1", "nvme2n1", "nvme3n1", "nvme4n1", "nvme5n1", "nvme6n1", "nvme7n1", "nvme8n1", "nvme9n1"]  # 10 devices

luks_params = {}
luks_params['default'] = ''
luks_params['no-queues'] = "--perf-no_read_workqueue --perf-no_write_workqueue"
luks_params['sub-crypt-cpus'] = "--perf-submit_from_crypt_cpus --perf-no_read_workqueue --perf-no_write_workqueue"
luks_params['same-cpu-crypt'] = "--perf-same_cpu_crypt --perf-no_read_workqueue --perf-no_write_workqueue"

def yes_or_no(question, default_no=True):
    if (not run_interactive):
        reply = 'n' if default_no else 'y'
        print("[auto] " + question + " - default: " + reply)
    else:
        choices = ' [y/N]: ' if default_no else ' [Y/n]: '
        default_answer = 'n' if default_no else 'y'
        reply = str(input(question + choices)).lower().strip() or default_answer

    if reply[0] == 'y':
        return True
    if reply[0] == 'n':
        return False
    else:
        return False if default_no else True

def blkdev_exists(path):
    try:
        return stat.S_ISBLK(os.stat(path).st_mode)
    except:
        return False

def setup_output_dir(enc = False, enc_param = ''):
    # output_base = "$HOME/benchmark/fio" -- bench_fio creates subdirs: devicename/mode(rxmix)/block_size
    output_luks = path.abspath(output_base if not enc else output_base + f'_luks')
    output_dir = output_luks if enc_param == '' else output_base + f'_' + enc_param
    supporting.make_directory(output_dir)
    return output_dir

def setup_luks_dev(device, luks_param):
    crypt_header = crypt_header_prefix + device + ".img"

    is_luks = subprocess.run(['cryptsetup', 'isLuks', device_prefix + device])

    #if not is_luks.returncode == 0:  # if it's not already a luks device
    #    print(f'Device {device} is not a valid luks device, quitting.')

    if (not os.path.exists(crypt_header)) or (not is_luks.returncode == 0):
        #no header file, no device crypt-header
        print(f'Configuring luks on {device}, creating crypt header: {crypt_header}...')
        cmd_fallocate = subprocess.run(['fallocate', '-l', '2M', crypt_header])
        #TODO: catch command failures gracefully
        cmd_luks_fmt = subprocess.run(['cryptsetup', '-q', 'luksFormat', device_prefix + device, '--batch-mode', '--header', crypt_header],
                                      input=crypt_pass, capture_output=True, text=True)
        print(cmd_luks_fmt.stdout)
    elif (os.path.exists(crypt_header)) and (is_luks.returncode == 0):
        print(f'Device {device} is a luks device, Header {crypt_header} exists.')
        is_open = subprocess.run(['dmsetup', 'info', 'encrypted-{device}'])
        if not is_open.returncode == 0:
            cmd_luks_open = subprocess.run(['cryptsetup', '-q', 'open', luks_param, '--header', crypt_header, f'{device_prefix}{device}', 'encrypted-{device}'],
                                           input=crypt_pass, capture_output=True, text=True)
            print(cmd_luks_open.stdout)
        else:
            print(f'Device: {device} is already open: /dev/mapper/encrypted-{device}')
            print(is_open.stdout)
    else:
        print(f'Something failed looking at the header (should be: {crypt_header} ) and device state for {device}')
        input("Press Enter to continue... ")

def close_luks_dev(device):
    #		cryptsetup close encrypted-${dev}
	#   	rm /var/tmp/crypthdr.img
    #TODO: Check if device is open before running close and removing header

    crypt_header = crypt_header_prefix + device + ".img"
    cmd_luks_close = subprocess.run(['cryptsetup', 'close', 'encrypted-{device}'], capture_output=True, text=True)
    if not cmd_luks_close.returncode == 0:
        print(f'Closing luks on {device} errored:' + str(cmd_luks_close.returncode))
        print(f'stderr: ' + cmd_luks_close.stderr)
        print(f'stdout: ' + cmd_luks_close.stdout)
        input("Press Enter to continue... ")
    
    if yes_or_no(f'Remove luks header: {crypt_header}?', False):
        cmd_rm_header = subprocess.run(['rm', '-vf', '{crypt_header}'], capture_output=True, text=True)
        print(cmd_rm_header.stdout)

# def run_fio_tests(device, enc = False, enc_param = '', jobsmultiplier = 1):
#     #./bench_fio --target /dev/md0 /dev/md1 --type device --mode randread randwrite --output RAID_ARRAY --destructive
#     #arg_target = f'--destructive --parallel --type {target_type} --target {device_prefix}' + f' {device_prefix}'.join(all_devs)
#     arg_mode = f'--mode ' + ' '.join(test_settings["mode"]) + ' --rwmixread ' + ' '.join(map(str, randrw_mix))
#     arg_runtime = f'--runtime {runtime}'
#     arg_blocksz = f'--block-size ' + ' '.join(block_size)
#     arg_iodepth = f'--iodepth ' + ' '.join(map(str, iodepths))
#     arg_numjobs = f'--numjobs ' + ' '.join(map(str, numjobs))
#     # output_base = "$HOME/benchmark/fio" -- bench_fio creates subdirs: devicename/mode(rxmix)/block_size
#     output_luks = output_base if not enc else output_base + f'_luks'
#     output_dir = (output_luks + f'_def') if enc_param == '' else output_base + f'_luks'
#     if enc: dirname += f'_luks_{enc_param}'

#     #arg_output = f'--output {output_base}/' + f'{}'
    
#     # Run tests with different parameters
#     for testmode in test_modes:
#         for iodepth in iodepths:
#             for numjob in numjobs:
#                 for blocksize in block_size:
#                     rwmixread=""
#                     if testmode is "randrw":
#                         for mix in randrw_mix:
#                             jobname = f"job-{testmode}_{mix}_iod{iodepth}_numjobs{numjob}_{blocksize}"
#                             if enc: jobname += f'_luks_{enc_param}'
#                             bench_fio(
#                                 job_name=jobname,
#                                 mode=testmode,
#                                 rwmixread=mix,
#                                 direct=1,
#                                 filename=device,
#                                 iodepth=iodepth,
#                                 numjobs=numjob * jobsmultiplier,
#                                 runtime=runtime,
#                                 bs=blocksize,
#                                 # Add other fio options as needed
#                             )
                            
#                     # Configure fio_bench arguments based on your needs
#                     jobname = f"job-{testmode}_iod{iodepth}_numjobs{numjob}_{blocksize}"
#                     if enc: jobname += f'_luks_{enc_param}'
#                     bench_fio(
#                         job_name=jobname,
#                         mode=testmode,
#                         direct=1,
#                         filename=device,
#                         iodepth=iodepth,
#                         numjobs=numjob * jobsmultiplier,
#                         runtime=runtime,
#                         bs=blocksize,
#                         # Add other fio options as needed
#                     )

def main():
#    args = sys.argv[1:]

#    if not args:
#        print('usage: [--flags options] [inputs] ')
#        sys.exit(1)
        # Encryption setup (if applicable)

    # from bench_fio:
    checks.check_encoding()
    checks.check_if_fio_exists()
    test_settings["target"] = one_nvme_dev
    test_settings["output"] = setup_output_dir()
    checks.check_settings(test_settings)
    tests = supporting.generate_test_list(test_settings)
    print("[debug] Tests: ", tests)
    
    if do_luks_tests:
        for cryptopt in test_crypt:        #["none", "default", "no-queues", "same-cpu-crypt"]
            test_settings["crypto"] = cryptopt
            test_settings["output"] = setup_output_dir(enc = True, enc_param=cryptopt)
            display.display_header(test_settings, tests)
            if cryptopt != "none":
                setup_luks_dev(test_settings, luks_params[cryptopt])
                runfio.run_benchmarks(test_settings, tests)
                close_luks_dev(test_settings["device"])
            else:
                runfio.run_benchmarks(test_settings, tests)
    else:
        display.display_header(test_settings, tests)
        runfio.run_benchmarks(test_settings, tests)

    # # Run benchmarks on single device
    # run_fio_tests(one_nvme_dev)
    # if do_luks_tests:
    #     for paramkey, param in luks_params.items():
    #         setup_luks_dev(all_devs[0], param)
    #         run_fio_tests(one_nvme_dev, True, paramkey)
    

    # run_fio_tests(all_nvme_dev)
    # for dev in all_devs:
    #     if do_luks_tests:
    #         for paramkey, param in luks_params.items():
    #             for dev in all_devs:
    #                 setup_luks_dev(dev, param)
    #             run_fio_tests(all_nvme_dev, True, paramkey)
            

# Main body
if __name__ == '__main__':
    main()
