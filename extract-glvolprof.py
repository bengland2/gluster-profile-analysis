#!/usr/bin/python
# -*- coding: utf-8 -*-

#
# extract-glvolprof.py
# written by Ben England 2015
# copyright is GNU GPL V3, for details read:
#   https://tldrlegal.com/license/gnu-general-public-license-v3-%28gpl-3%29#fulltext
#
# script to read gluster volume profile output retrieved every N seconds
# and generate operation rate graph from it
#
# see gvp-README.html in the same directory for directions on use.
#
# Note: this tool uses a snapshot of javascript code from this project:
#   https://github.com/distributed-system-analysis/pbench
# but we do not support any use of this software outside of the graphing 
# of the data generated below.  The Save Image button does not work yet
#
# input:
#  this script expects input data to look like what the gvp.sh script (in same
#  directory) produces:
#
#  record 1 is a timestamp in format YYYY-MM-DD-HH-MM
#  record 2 contains the user-specified sample interval and count
#  used by gvp.sh.
#  subsequent "gluster volume profile your-volume info" outputs are 
#  concatenated to the profile log.  
#  Each profile sample happens exactly N seconds after
#  the preceding sample, where N is the gvp.sh sampling interval.
#  seconds.  The first sample happens N seconds after the timestamp.
#
# output:
#
#  when we're all done reading in data,
#  we then print it out in a format suitable for spreadsheet-based graphing
#
# internals:
#
# the "intervals" array, indexed by interval number, stores results over time
# within each array element,
# we have a dictionary indexed by brick name containing BrickProfile instances
# these in turn contain a dictionary of BrickFopProfile instances
# to represent the per-FOP records in "gluster volume profile" output
#
# the per-brick dictionary is indexed by a string
# starting with 'cumul' or 'intvl' and ending with the FOP name
# this isn't strictly necessary but provides latent support
# for someday including cumulative stats as well as per-interval stats
#
# stats for the entire volume are rolled up using call rates for weighted averaging
#

import sys
import os
from os.path import join
import re
import time
import shutil
import collections

# fields in gluster volume profile output

time_duration_types = ['cumulative', 'interval']
stat_names = ['pct-lat', 'avg-lat', 'min-lat', 'max-lat', 'call-rate']
directions = ['MBps-read', 'MBps-written']
min_lat_infinity = 1.0e24

# this environment variable lets you graph .csv files using pbench

pbench_graphs = True
if os.getenv('SKIP_PBENCH_GRAPHING'): pbench_graphs = False

# this is the list of graphs that will be produced

graph_csvs = [
    ('vol_call-rate_allfop', 'volume-level FOP call rates'),
    ('vol_pct-lat_allfop', 'percentage server-side latency by FOP'),
    ('MBps-written-volume', 'MB/sec written to Gluster volume'), 
    ('MBps-read-volume', 'MB/sec read from Gluster volume'),
    ('MBps-written-bricks', 'MB/sec written to Gluster bricks'), 
    ('MBps-read-bricks', 'MB/sec read from Gluster bricks')
]

# all gvp.sh-generated profiles are expected to have these parameters
# we define them here to have global scope, and they are only changed
# by the input parser

start_time = None
expected_duration = None
expected_sample_count = None
sorted_fop_names = None
sorted_brick_names = None
intervals = None

# this class stores per-fop statistics from gluster volume profile output
# to compute stats for %latency and average latency across a set of bricks,
# we have to compute averages weighted by brick usage.
# We do this in two steps:
# - loop over set of instances and compute weighted sum (not average)
# - after loop, normalize using total calls


class BrickFopProfile:

    def __init__(self, pct_lat, avg_lat, min_lat, max_lat, calls):
        self.pct_lat = pct_lat
        self.avg_lat = avg_lat
        self.min_lat = min_lat
        self.max_lat = max_lat
        self.calls = calls

    def __str__(self):
        return '%6.2f, %8.0f, %8.0f, %8.0f, %d' % (
            self.pct_lat, self.avg_lat, self.min_lat, self.max_lat, self.calls)

    # append a single field to .csv record based on statistic type
    # use "-6.2f" instead of "%6.2f" so there are no leading spaces in record,
    # otherwise spreadsheet inserts colums at col. B

    def field2str(self, stat, duration):
        if stat == stat_names[0]:
            return '%-6.2f' % self.pct_lat
        elif stat == stat_names[1]:
            return '%8.0f' % self.avg_lat
        elif stat == stat_names[2]:
            if self.min_lat == min_lat_infinity:
                return ''  # don't confuse spreadsheet/user
            else:
                return '%8.0f' % self.min_lat
        elif stat == stat_names[3]:
            if self.max_lat == 0:
                return ''
            else:
                return '%8.0f' % self.max_lat
        elif stat == stat_names[4]:
            call_rate = self.calls / float(duration)
            return '%9.2d' % call_rate

    # accumulate weighted sum of component profiles, will normalize them later

    def accumulate(self, addend):
        self.pct_lat += (addend.pct_lat * addend.calls)
        self.avg_lat += (addend.avg_lat * addend.calls)
        if addend.calls > 0:
            self.max_lat = max(self.max_lat, addend.max_lat)
            self.min_lat = min(self.min_lat, addend.min_lat)
        self.calls += addend.calls

    # normalize weighted sum to get averages

    def normalize_sum(self):
        try:
            # totals will become averages
            self.pct_lat /= self.calls
            self.avg_lat /= self.calls
        except ZeroDivisionError:  # if no samples, set these stats to zero
            self.pct_lat = 0.0
            self.avg_lat = 0.0


def zero_bfprofile():
    # variable to accumulate stats across all bricks
    # for min, use some very large number
    # that latency will never exceed so that
    # min(lat, all_min_lat) == lat
    # same for max, use a lower bound for latency (0)
    # so max(lat, all_max_lat) = lat
    return BrickFopProfile(0.0, 0.0, min_lat_infinity, 0.0, 0)


# this class stores per-brick results

class BrickProfile:

    def __init__(self):
        self.bytes_read = 0
        self.bytes_written = 0
        self.interval = 0  # seconds, so DivisionByZero exception if not set
        # BrickFopProfile results stored in dictionary indexed by FOP name
        self.per_fop = {}

    def __str__(self):
        return '%d, %d, %s' % (
            self.bytes_read, self.bytes_written, str(self.per_fop))


# if there is an error parsing the input...

def usage(msg):
    print('ERROR: %s' % msg)
    print('usage: extract-glvolprof.py your-gluster-volume-profile.log')
    sys.exit(1)


# because we produce so many .csv files, segregate them into a separate output
# directory with pathname derived from the input log file with _csvdir suffix

def make_out_dir(path):
    dir_path = path + '_csvdir'
    try:
        if os.path.exists(dir_path):
            shutil.rmtree(dir_path)
        os.mkdir(dir_path)
    except IOError:
        usage('could not (re-)create directory ' + dir_path)
    return dir_path


# convert gluster volume profile output
# into a time series of per-brick per-fop results.

def parse_input(input_pathname):
    global start_time
    global expected_sample_interval
    global expected_sample_count
    global sorted_fop_names
    global sorted_brick_names
    global intervals

    try:
        with open(input_pathname, 'r') as file_handle:
            lines = [ l.strip() for l in file_handle.readlines() ]
    except IOError:
        usage('could not read ' + input_pathname)
    start_time = time.mktime(time.strptime(lines[0], '%Y-%m-%d-%H-%M')) * 1000
    tokens = lines[1].split()
    expected_sample_interval = int(tokens[0])
    expected_sample_count = int(tokens[1])
    print('collection started at %s' % lines[0])
    print('sampling interval is %d seconds' % expected_sample_interval)
    print('expected sample count is %d samples' % expected_sample_count)

    # parse the file and record each cell of output in a way that lets you
    # aggregate across bricks later

    found_cumulative_output = False
    found_interval_output = False
    all_caps_name = re.compile('.*[A-Z]+$')
    fop_names = set()
    last_intvl = -1
    intvl = -1
    per_op_table = {}
    sample = -1
    intervals = []
    bricks_seen = {}
    duration = None
    for ln in lines[2:]:
        tokens = ln.strip().split()

        if ln.startswith('Brick:'):

            brick_name = tokens[1]
            try:
                brick_count = bricks_seen[brick_name]
            except KeyError:
                brick_count = 0
            if brick_count == intvl + 1:
                intvl += 1
            else:
                assert brick_count == intvl
            brick_count += 1
            bricks_seen[brick_name] = brick_count

        elif ln.__contains__('Interval') and ln.__contains__('Stats'):

            assert intvl == last_intvl or intvl == last_intvl + 1
            last_intvl = intvl
            found_interval_output = True

        elif ln.__contains__('Cumulative Stats'):

            found_cumulative_output = True

        elif ln.__contains__('Duration:'):

            # we are at end of output for this brick and interval

            assert found_cumulative_output ^ found_interval_output
            duration = int(tokens[1])
            if found_interval_output and \
               abs(duration - expected_sample_interval) > 1:
                print(('WARNING: in sample %d brick %d the sample ' +
                       'interval %d deviates from expected value %d') %
                      (brick_count, sample, duration, expected_sample_interval))
            bricks_in_interval = intervals[intvl]
            brick = bricks_in_interval[brick_name]
            brick.interval = duration
            for fop in fop_names:
                for typ in time_duration_types:
                    k = fop + '.' + typ
                    try:
                        bfprofile = brick.per_fop[k]
                    except KeyError:
                        bfprofile = zero_bfprofile()
                        brick.per_fop[k] = bfprofile

        elif ln.__contains__('Data Read:'):

            bytes_read = int(tokens[2])
            per_brick_info = bricks_in_interval[brick_name]
            per_brick_info.bytes_read = bytes_read

        elif ln.__contains__('Data Written'):

            bytes_written = int(tokens[2])
            per_brick_info = bricks_in_interval[brick_name]
            per_brick_info.bytes_written = bytes_written

            # this is the end of per-brick results

            found_interval_output = False
            found_cumulative_output = False

        elif (found_interval_output or found_cumulative_output) \
             and all_caps_name.match(ln):

            # we found a record we're interested in,
            # accumulate table of data for each gluster function

            sample += 1
            new_bfprofile = BrickFopProfile(
                    float(tokens[0]), float(tokens[1]), float(tokens[3]),
                    float(tokens[5]), int(tokens[7]))
            op_name = tokens[8]

            # op name is a key into dictionary,
            # we record both per-interval and cumulative stats

            fop_names.add(op_name)

            if found_interval_output:  # keep cum. and interval stats separated
                op_name += '.' + time_duration_types[1]
            elif found_cumulative_output:
                op_name += '.' + time_duration_types[0]
            else:
                raise Exception('FOP-like string %s found outside stats'
                                 % op_name)

            if len(intervals) == intvl:
                bricks_in_interval = {}
                intervals.append(bricks_in_interval)
            elif len(intervals) == intvl + 1:
                bricks_in_interval = intervals[intvl]
            else:
                raise Exception(('intervals table length %d ' +
                                 'does not match interval number %d')
                                 % (len(intervals), intvl))

            try:
                fop_stats = bricks_in_interval[brick_name].per_fop
            except KeyError:
                bricks_in_interval[brick_name] = BrickProfile()
                fop_stats = bricks_in_interval[brick_name].per_fop

            fop_stats[op_name] = new_bfprofile

    sorted_brick_names = sorted(bricks_seen.keys())
    sorted_fop_names = sorted(fop_names)
    return (start_time, intervals)


# generate timestamp_ms column for pbench 
# given starting time of collection, sampling interval and sample number

def gen_timestamp_ms(sample_index):
    return start_time + ((expected_sample_interval * sample_index) * 1000)


# generate denominator for call rate computation based on duration type
# can't use brick.interval

def get_interval(duration_type, interval_index):
    if duration_type == 'cumulative':
        return interval_index * expected_sample_interval
    else:
        return expected_sample_interval

# display bytes read and bytes written per brick and for entire volume
# in separate graphs.  If we put them in the same graph in a volume with
# 16 bricks, for example, all you'll see is the per-volume number
# normalize to MB/s with 3 decimal places so 1 KB/s/brick will show

def gen_output_bytes(out_dir_path, duration_type):
    bytes_per_MB = 1000000.0
    final_brick_ct = len(sorted_brick_names)
    for direction in directions:
      per_vol_filename = direction + '-volume.csv'
      per_vol_pathname = join(out_dir_path, per_vol_filename)
      with open(per_vol_pathname, 'w') as total_transfer_fh:
        # when we support cumulative data, then we can name files this way
        #direction_filename = duration_type + '_' + direction + '.csv'
        per_brick_filename = direction + '-bricks.csv'
        per_brick_pathname = join(out_dir_path, per_brick_filename)
        with open(per_brick_pathname, 'w') as transfer_fh:
            if pbench_graphs: 
                transfer_fh.write('timestamp_ms, ')
                total_transfer_fh.write('timestamp_ms, ')
            transfer_fh.write(','.join(sorted_brick_names))
            total_transfer_fh.write('all\n')
            transfer_fh.write('\n')
            intvl = 0
            for bricks_in_interval in intervals:
                if pbench_graphs:
                    transfer_fh.write('%d, ' % gen_timestamp_ms(intvl))
                intvl += 1
                rate_interval = get_interval(duration_type, intvl) 
                total_transfer = 0
                columns = []
                for b in sorted_brick_names:  # for each brick
                    brick = bricks_in_interval[b]
                    if direction.__contains__('read'):
                        transfer = brick.bytes_read
                    else:
                        transfer = brick.bytes_written
                    total_transfer += transfer
                    columns.append( '%-8.3f ' % ((transfer/rate_interval)/bytes_per_MB))
                transfer_fh.write(','.join(columns) + '\n')
                total_transfer_fh.write('%d, %-9.3f\n' % (
                        gen_timestamp_ms(intvl),
                        (total_transfer/rate_interval)/bytes_per_MB))


# display per-FOP (file operation) stats,
# both per brick and across all bricks

def gen_per_fop_stats(out_dir_path, duration_type, stat):
    vol_fop_intervals = []
    for fop in sorted_fop_names:
        #per_fop_filename = duration_type + '_' + stat + '_' + fop + '.csv'
        per_fop_filename = 'brick_' + stat + '_' + fop + '.csv'
        per_fop_path = join(out_dir_path, per_fop_filename)
        with open(per_fop_path, 'a') as fop_fh:
            hdr = ''
            if pbench_graphs:
                hdr += 'timestamp_ms, '
            hdr += ','.join(sorted_brick_names)
            hdr += 'all\n'
            fop_fh.write(hdr)
            for i in range(0, len(intervals)):
                if pbench_graphs:
                    fop_fh.write('%d, ' % gen_timestamp_ms(i))
                bricks_in_interval = intervals[i]
                all_bfprofile = zero_bfprofile()
                columns = []
                for b in sorted_brick_names:  # for each brick
                    brick = bricks_in_interval[b]
                    try:
                        fop_stats = brick.per_fop[fop + '.' + duration_type]
                    except KeyError:
                        fop_stats = zero_bfprofile()
                    columns.append(fop_stats.field2str(stat, brick.interval))
                    all_bfprofile.accumulate(fop_stats)
                fop_fh.write('%s\n' % ','.join(columns))

                # collect FOP results across all bricks for later

                all_bfprofile.normalize_sum()
                if len(vol_fop_intervals) == i:
                    vol_fop_interval = {}
                    vol_fop_intervals.append(vol_fop_interval)
                else:
                    vol_fop_interval = vol_fop_intervals[i]
                vol_fop_interval[fop] = all_bfprofile
    return vol_fop_intervals

def gen_fop_summary(dir_path, duration_type, stat, vol_fop_intervals):
    #vol_fop_profile_path = join(dir_path, duration_type + '_' + stat + '_allfop.csv')
    vol_fop_profile_path = join(dir_path, 'vol_' + stat + '_allfop.csv')
    with open(vol_fop_profile_path, 'w') as vol_fop_fh:
        if pbench_graphs:
            vol_fop_fh.write('timestamp_ms, ')
        vol_fop_fh.write(','.join(sorted_fop_names))
        vol_fop_fh.write('\n')
        for i in range(0, len(vol_fop_intervals)):
            if pbench_graphs:
                vol_fop_fh.write('%d, ' % gen_timestamp_ms(i))
            vol_fop_profile_interval = vol_fop_intervals[i]
            if duration_type == 'cumulative':
                sample_interval = (i + 1) * expected_sample_interval
            else:
                sample_interval = expected_sample_interval
            columns = []
            for fop in sorted_fop_names:
                per_vol_fop_profile = vol_fop_profile_interval[fop]
                columns.append(
                    per_vol_fop_profile.field2str(
                        stat, sample_interval))
            vol_fop_fh.write('%s\n' % ','.join(columns))


# generate graphs in 
# generate output files in separate directory from
# data structure returned by parse_input

next_graph_template='''
    <div class="chart">
      <h3 class="chart-header">%s
        <button id="save1">Save as Image</button>
        <div id="svgdataurl1"></div>
      </h3>
      <svg id="chart%d"></svg>
      <canvas id="canvas1" style="display:none"></canvas>
      <script>
        constructChart("lineChart", %d, "%s", 0.00);
      </script>
    </div>
'''

def output_next_graph(graph_fh, gr_index):
    (csv_filename, graph_description) = graph_csvs[gr_index]
    gr_index += 1  # graph numbers start at 1
    graph_fh.write( next_graph_template % (
                    graph_description, gr_index, gr_index, csv_filename))

# static content of HTML file

header='''
<!DOCTYPE HTML>
<html>
  <head>
    <meta charset="utf-8">
    <link href="static/css/v0.2/nv.d3.css" rel="stylesheet" type="text/css" media="all">
    <link href="static/css/v0.2/pbench_utils.css" rel="stylesheet" type="text/css" media="all">
    <script src="static/js/v0.2/function-bind.js"></script>
    <script src="static/js/v0.2/fastdom.js"></script>
    <script src="static/js/v0.2/d3.js"></script>
    <script src="static/js/v0.2/nv.d3.js"></script>
    <script src="static/js/v0.2/saveSvgAsPng.js"></script>
    <script src="static/js/v0.2/pbench_utils.js"></script>
  </head>
  <body class="with-3d-shadow with-transitions">
    <h2 class="page-header">gluster volume profile - summary graphs</h2>
'''

trailer='''
  </body>
</html>
'''


# generate graphs using header, trailer and graph template

def gen_graphs(out_dir_path):
    graph_path = join(out_dir_path, 'gvp-graphs.html')
    with open(graph_path, 'w') as graph_fh:
        graph_fh.write(header)
        for j in range(0, len(graph_csvs)):
            output_next_graph(graph_fh, j)
        graph_fh.write(trailer)


# make link to where javascript etc lives in unpacked tarball
# ASSUMPTION is that output directory is a subdirectory of where this script
# lives (not a sub-subdirectory).  Sorry but that's the only way to generate a
# softlink that works when we copy the csvdir to a different location.

def gen_static_softlink(out_dir_path):
    saved_cwd = os.getcwd()
    static_dir = join(saved_cwd, 'static')
    if not os.path.exists(static_dir):
        print('ERROR: sorry, the javascript directory "static" ' + 
              'needs to be in same directory as this script, trying anyway...')
    os.chdir(out_dir_path)
    os.symlink(join('..', 'static'), 'static')
    os.chdir(saved_cwd)

# generate everything needed to view the graphs

def generate_output(out_dir_path):

    for t in [ 'interval' ]:  # cumulative doesn't work yet
        gen_output_bytes(out_dir_path, t)
        for s in stat_names:
            vol_fop_intvls = gen_per_fop_stats(out_dir_path, t, s)
            gen_fop_summary(out_dir_path, t, s, vol_fop_intvls)

    gen_graphs(out_dir_path)
    gen_static_softlink(out_dir_path)

    sys.stdout.write('Gluster FOP types seen: ')
    for fop_name in sorted_fop_names:
        sys.stdout.write(' ' + fop_name)
    sys.stdout.write('\n')
    sys.stdout.write('Gluster bricks seen: ')
    for brick_name in sorted_brick_names:
        sys.stdout.write(' ' + brick_name)
    sys.stdout.write('\n')
    print('created Gluster statistics files in directory %s' % out_dir_path)
    print('graphs now available at browser URL file://%s/%s/gvp-graphs.html' \
          % (os.getcwd(), out_dir_path))


# the main program is kept in a subroutine so that it can run on Windows.

def main():
    if len(sys.argv) < 2:
        usage('missing gluster volume profile output log filename parameter'
              )
    fn = sys.argv[1]
    parse_input(fn)
    outdir = make_out_dir(fn)
    generate_output(outdir)

main()
