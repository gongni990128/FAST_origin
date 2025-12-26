import json
import datetime
import logging
import math
from dataclasses import dataclass
from os import listdir, makedirs
from os.path import isfile, join, abspath, dirname, exists
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass
class RuntimeConfig:
    realtime_mode: bool = False
    buffer_size: int = 0
    concurrency: Optional[int] = None
    latency_threshold: float = 0.0

    def effective_concurrency(self, default: int) -> int:
        if self.concurrency is None or self.concurrency <= 0:
            return default
        return self.concurrency


class RuntimeMetrics:
    def __init__(self, runtime_config: RuntimeConfig):
        self.runtime_config = runtime_config

    def log_queue_length(self, queue_length: int) -> None:
        logger.info(
            "runtime.queue_length=%d buffer_size=%d realtime_mode=%s",
            queue_length,
            self.runtime_config.buffer_size,
            self.runtime_config.realtime_mode,
        )

    def log_window_duration(self, duration_sec: float) -> None:
        logger.info(
            "runtime.window_duration_sec=%.3f latency_threshold=%.3f",
            duration_sec,
            self.runtime_config.latency_threshold,
        )
        if self.runtime_config.latency_threshold and (
            duration_sec > self.runtime_config.latency_threshold
        ):
            logger.warning(
                "runtime.window_duration_exceeded duration_sec=%.3f threshold=%.3f",
                duration_sec,
                self.runtime_config.latency_threshold,
            )

    def log_fingerprint_output(self, count: int, duration_sec: float) -> None:
        rate = 0.0
        if duration_sec > 0:
            rate = count / duration_sec
        logger.info(
            "runtime.fingerprint_output count=%d rate_per_sec=%.3f",
            count,
            rate,
        )

    def log_mad_update(self, duration_sec: float) -> None:
        logger.info("runtime.mad_update_sec=%.3f", duration_sec)


def parse_json(param_json):
    with open(param_json) as json_data_file:
        params = json.load(json_data_file)
    return params


def add_runtime_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--stream",
        action="store_true",
        help="Enable realtime/streaming mode",
    )
    group.add_argument(
        "--batch",
        action="store_true",
        help="Force batch/offline mode (default)",
    )
    parser.add_argument(
        "--buffer-size",
        type=int,
        help="Queue buffer size used when dispatching fingerprint tasks",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        help="Override worker concurrency for fingerprint generation",
    )
    parser.add_argument(
        "--latency-threshold",
        type=float,
        help="Soft latency budget (seconds) for processing a window",
    )


def _normalize_runtime_block(runtime_block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if runtime_block is None:
        runtime_block = {}
    return runtime_block


def apply_runtime_overrides(params: Dict[str, Any], args: Any) -> Dict[str, Any]:
    runtime_block = _normalize_runtime_block(params.get("runtime"))
    if getattr(args, "stream", False):
        runtime_block["realtime_mode"] = True
    if getattr(args, "batch", False):
        runtime_block["realtime_mode"] = False
    if getattr(args, "buffer_size", None) is not None:
        runtime_block["buffer_size"] = args.buffer_size
    if getattr(args, "concurrency", None) is not None:
        runtime_block["concurrency"] = args.concurrency
    if getattr(args, "latency_threshold", None) is not None:
        runtime_block["latency_threshold"] = args.latency_threshold
    params["runtime"] = runtime_block
    return params


def runtime_args_to_list(args: Any, defaults: Optional[Dict[str, Any]] = None) -> Iterable[str]:
    defaults = _normalize_runtime_block(defaults)
    cli_args = []
    mode = None
    if getattr(args, "stream", False):
        mode = "stream"
    elif getattr(args, "batch", False):
        mode = "batch"
    elif "realtime_mode" in defaults:
        mode = "stream" if defaults["realtime_mode"] else "batch"
    if mode == "stream":
        cli_args.append("--stream")
    elif mode == "batch":
        cli_args.append("--batch")

    def _append_arg(attr_name: str, key: str, flag: str):
        value = getattr(args, attr_name, None)
        if value is None:
            value = defaults.get(key)
        if value is not None:
            cli_args.extend([flag, str(value)])

    _append_arg("buffer_size", "buffer_size", "--buffer-size")
    _append_arg("concurrency", "concurrency", "--concurrency")
    _append_arg("latency_threshold", "latency_threshold", "--latency-threshold")
    return cli_args


def get_runtime_config(params: Dict[str, Any]) -> RuntimeConfig:
    runtime_block = _normalize_runtime_block(params.get("runtime"))
    return RuntimeConfig(
        realtime_mode=runtime_block.get("realtime_mode", False),
        buffer_size=runtime_block.get("buffer_size", 0) or 0,
        concurrency=runtime_block.get("concurrency"),
        latency_threshold=float(runtime_block.get("latency_threshold", 0.0) or 0.0),
    )


def should_include_file(f, params):
    return params['data']['station'] in f and \
        params['data']['channel'] in f


def init_folder(folders):
    for folder in folders:
        if not exists(folder):
            makedirs(folder)


def get_fp_ts_folders(params):
    fp_folder = params['data']['folder'] + 'fingerprints/'
    ts_folder = params['data']['folder'] + 'timestamps/'
    return fp_folder, ts_folder


def get_ts_fname(mseed_fname):
    idx = mseed_fname.rfind('.')
    return "ts_" + mseed_fname[:idx]


def get_fp_fname(mseed_fname):
    idx = mseed_fname.rfind('.')
    return "fp_" + mseed_fname[:idx]


def get_combined_fp_name(params):
    final_fp_name = '%s.%s.fp' % (
        params['data']['station'], params['data']['channel'])
    return final_fp_name


def get_combined_ts_name(params):
    final_ts_name = '%s.%s.ts' % (
        params['data']['station'], params['data']['channel'])
    return final_ts_name


def get_fp_stats_file(params):
    return '%s%s_%s.json' %(params["data"]["folder"], params["data"]["station"],
        params["data"]["channel"])


def save_fp_stats(params, nfp, ndim):
    fp_stats = {"station": params["data"]["station"],
        "channel": params["data"]["channel"],
        "nfp": nfp,
        "ndim": ndim}
    fname = get_fp_stats_file(params)
    with open(fname, 'w') as f:
        json.dump(fp_stats, f)


def get_data_files(params):
    path = abspath(join(dirname(__file__), params['data']['folder']))
    files = []
    for f in listdir(path):
        if isfile(join(path, f)) and should_include_file(f, params):
            files.append(f)
    return files


def get_min_fp_length(params):
    return params['fingerprint']['fp_length'] * params['fingerprint']['spec_lag'] + \
        params['fingerprint']['spec_length']


def get_start_end_times(params):
    start_time = datetime.datetime.strptime(params['data']['start_time'], 
        "%y-%m-%dT%H:%M:%S.%f")
    end_time = datetime.datetime.strptime(params['data']['end_time'], 
        "%y-%m-%dT%H:%M:%S.%f")
    return (start_time, end_time)


def gen_mad_fname(params):
    mad_folder = params['data']['folder'] + 'mad/'
    init_folder([mad_folder])
    return mad_folder + 'mad%s_%s_%f_%.0f_%s_%s.txt' % (
        params['data']['station'],
        params['data']['channel'],
        params['fingerprint']['mad_sampling_rate'],
        params['fingerprint']['mad_sample_interval'],
        params['data']['start_time'],
        params['data']['end_time'] )


# Return the largest power of 2 less than or equal to n
def lower_power_2(n):
   return 2**(int(math.log(n, 2)))


def get_ntimes(params):
    return lower_power_2(params['fingerprint']['fp_length'])


def get_partition_padding(params):
    # add this to end of time series of each partition so we don't have missing fingerprints
    sec_extra = params['fingerprint']['spec_length'] + \
        (params['fingerprint']['fp_length'] - params['fingerprint']['fp_lag']) * \
        params['fingerprint']['spec_lag']
    time_extra = datetime.timedelta(seconds=sec_extra)
    return time_extra
