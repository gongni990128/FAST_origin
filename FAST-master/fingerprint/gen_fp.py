import argparse
import logging
import os
import subprocess
import sys
import time
from multiprocessing import Pool

from config import (
    RuntimeMetrics,
    apply_runtime_overrides,
    get_runtime_config,
)
from config import *

madStatsCommand= 'python MAD.py %s'
fingerprintCommand = 'python finger_print.py %s %s'
runtime_cli = ""


def call_fingerprint(args):
	cmd = fingerprintCommand % (args, param_json)
	if runtime_cli:
		cmd = f"{cmd} {runtime_cli}"
	process = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
	process.communicate()


def call_mad(param_json):
	print("Processing for MAD")
	cmd = madStatsCommand % (param_json)
	if runtime_cli:
		cmd = f"{cmd} {runtime_cli}"
	process = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
	output, error = process.communicate()
	print(output.decode('UTF-8').strip())


if __name__ == '__main__':
	logging.basicConfig(level=logging.INFO)
	parser = argparse.ArgumentParser()
	parser.add_argument("param_json", help="Fingerprint parameter JSON file")
	add_runtime_arguments(parser)
	args = parser.parse_args()

	param_json = args.param_json
	params = apply_runtime_overrides(parse_json(param_json), args)
	runtime_config = get_runtime_config(params)
	metrics = RuntimeMetrics(runtime_config)
	global runtime_cli
	runtime_cli = " ".join(runtime_args_to_list(args, defaults=params.get("runtime")))

	# Preprocess to calculate MAD
	t_mad_start = time.time()
	call_mad(param_json)
	metrics.log_mad_update(time.time() - t_mad_start)

	# Fingerprint
	files = params['data']['fingerprint_files']
	metrics.log_queue_length(len(files))
	worker_count = runtime_config.effective_concurrency(
		params['performance']['num_fp_thread'])
	if worker_count > 0:
		pool = Pool(min(worker_count, len(files)))
		pool.map(call_fingerprint, files)
	else:
		for f in files:
			call_fingerprint(f)

	# Stich fingerprint files
	nfp = 0
	ntimes = get_ntimes(params)
	fp_in_bytes = params['fingerprint']['nfreq'] * ntimes / 4
	fp_path, ts_path = get_fp_ts_folders(params)
	final_fp_name = '%s%s' %(fp_path, get_combined_fp_name(params))
	if os.path.exists(final_fp_name):
		os.remove(final_fp_name)
	print("Combining into final fingerprint file %s" % final_fp_name)

	final_ts_name = '%s%s' %(ts_path, get_combined_ts_name(params))
	if os.path.exists(final_ts_name):
		os.remove(final_ts_name)
	print("Combining into final timestamp file %s" % final_ts_name)

	for fname in files:
		fp_file = fp_path + get_fp_fname(fname)
		os.system("cat %s >> %s" % (fp_file, final_fp_name))

		ts_file = ts_path + get_ts_fname(fname)
		os.system("cat %s >> %s" % (ts_file, final_ts_name))

		# Verify number of fingerprints
		num_lines = sum(1 for line in open(ts_file))
		nfp += num_lines
		fsize = os.path.getsize(fp_file)
		if fsize / fp_in_bytes != num_lines:
			print("Exception: # fingerprints in %s don't match" % fname)
			print("Fingerprint file: %d, timestamp file: %d" %(fsize / fp_in_bytes, num_lines))
			exit(1)

	fsize = os.path.getsize(final_fp_name)
	print("Fingerprint file size: %d bytes" % (fsize))
	print("# fingerprints: %d" %(nfp))
	ndim = fsize * 8 / nfp

	# Save fingerprint stats
	save_fp_stats(params, nfp, ndim)
