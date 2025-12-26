import argparse
import logging
import sys
import time

import numpy as np
from obspy import read
from obspy.core import UTCDateTime

from config import RuntimeMetrics, add_runtime_arguments, apply_runtime_overrides, get_runtime_config
from config import *
from feature_extractor import *

def write_timestamp(t, idx1, idx2, starttime, ts_file):
	fp_timestamp = np.asarray([t[int(np.mean((idx1[j], idx2[j])))] for j in range(len(idx1))])
	for ts in fp_timestamp:
		ts_file.write((starttime + datetime.timedelta(seconds = ts)).strftime('%y-%m-%dT%H:%M:%S.%f') + '\n')


def normalize_and_fingerprint(haar_images, fp_file):
	std_haar_images = feats.standardize_haar(haar_images, type = 'MAD')
	binaryFingerprints =  feats.binarize_vectors_topK_sign(std_haar_images,
		K = params['fingerprint']['k_coef'])
	# Write to file
	b = np.packbits(binaryFingerprints)
	fp_file.write(b.tobytes())


def init_MAD_stats(mad_fname):
	ntimes = get_ntimes(params)
	feats.haar_medians = np.zeros(params['fingerprint']['nfreq'] * ntimes)
	feats.haar_absdevs = np.zeros(params['fingerprint']['nfreq'] * ntimes)
	f = open(mad_fname, 'r')
	for i, line in enumerate(f.readlines()):
		nums = line.split(',')
		feats.haar_medians[i] = float(nums[0])
		feats.haar_absdevs[i] = float(nums[1])
	f.close()


if __name__ == '__main__':
	logging.basicConfig(level=logging.INFO)
	t_start = time.time()
	parser = argparse.ArgumentParser()
	parser.add_argument("fname", help="Input waveform filename")
	parser.add_argument("param_json", help="Fingerprint parameter JSON file")
	add_runtime_arguments(parser)
	args = parser.parse_args()

	fname = args.fname
	param_json = args.param_json
	params = apply_runtime_overrides(parse_json(param_json), args)
	runtime_config = get_runtime_config(params)
	metrics = RuntimeMetrics(runtime_config)

	feats = init_feature_extractor(params, get_ntimes(params))

	mad_fname = gen_mad_fname(params)
	init_MAD_stats(mad_fname)

	fp_folder, ts_folder = get_fp_ts_folders(params)
	init_folder([fp_folder, ts_folder])

	# read mseed
	st = read(params['data']['folder'] + fname)
	ts_file = open(ts_folder + get_ts_fname(fname), "w")
	fp_file = open(fp_folder + get_fp_fname(fname), "wb")
	time_padding = get_partition_padding(params)
	min_fp_length = get_min_fp_length(params)

	total_fp = 0
	for i in range(len(st)):
		# Get start and end time of the current continuous segment
		starttime = datetime.datetime.strptime(str(st[i].stats.starttime), '%Y-%m-%dT%H:%M:%S.%fZ')
		endtime = datetime.datetime.strptime(str(st[i].stats.endtime), '%Y-%m-%dT%H:%M:%S.%fZ')
		# Ignore segments that are shorter than one spectrogram window length
		if endtime - starttime < datetime.timedelta(seconds = min_fp_length):
			continue

		s = starttime
		# Generate and output fingerprints per partition_len
		while endtime - s > datetime.timedelta(seconds = min_fp_length):
			dt = datetime.timedelta(
				seconds = params['performance']['partition_len'])
			e = min(s + dt, endtime)
			e_padding = min(s + dt + time_padding, endtime)
			partition_st = st[i].slice(UTCDateTime(s.strftime('%Y-%m-%dT%H:%M:%S.%f')),
				UTCDateTime(e_padding.strftime('%Y-%m-%dT%H:%M:%S.%f')))
			# Spectrogram + Wavelet transform
			window_start = time.time()
			haar_images, nWindows, idx1, idx2, Sxx, t  = feats.data_to_haar_images(partition_st.data)
			# Write fingerprint time stamps to file
			write_timestamp(t, idx1, idx2, s, ts_file)
			# Normalize and output fingerprints on a roughly 8 hour time interval
			normalize_and_fingerprint(haar_images, fp_file)
			metrics.log_window_duration(time.time() - window_start)
			total_fp += len(haar_images)
			s = e

	ts_file.close()
	fp_file.close()

	t_end = time.time()
	total_duration = t_end - t_start
	metrics.log_fingerprint_output(total_fp, total_duration)
	print("Binary fingerprints took: %.2f seconds" % (total_duration))
