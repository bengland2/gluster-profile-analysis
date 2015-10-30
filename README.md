# gluster-profile-analysis
tools for converting gluster profile data into spreadsheet format and javascript graphs

## Introduction

The extract-glvolprof.py program is meant to assist with visualizing the performance of
a gluster volume, using the gluster volume profile command.  One of key concepts in Gluster is the FOP (File Operation).  This is the unit of work passed from the application down through the Gluster translator stack until it reaches the storage device.  Important FOP Types include:

- CREATE - create a file
- UNLINK - delete a file
- OPEN - open a file for read/write access
- WRITE - write data to a file
- READ - read data from a file
- LOOKUP - lookup a file
- MKDIR - create a directory
- RMDIR - remove a directory

Statistic types produced per FOP type by these scripts include:

- call rates - for example, how many requests of different types are made per sec
- % latency - what fraction of FOP response time is consumed by different FOP types
- avg. latency - average FOP response time
- minimum latency
- maximum latency

Where all latencies are in units of microseconds.

These tools produce a subdirectory containing java-script graphs that can be viewed with a web browser, as well as .csv-format files that can be loaded into a spreadsheet, for example.  BTW, not everything works: e.g. the "Save as Image" button does not. Note also that the layout is crucial: the CSV subdirectory contains a
symlink <code>static</code>, which points to the <code>static</code> subdirectory in the
main directory (which is where the javascript tarball was unpacked). If you change that structure, then the javascript files may not be found: no graphs!

# server-side profiling

Server-side profiling allows you to see activity across the entire Gluster volume for a specified number of periodic samples.  It also allows you to see variation in stats between bricks, which can help you identify hotspots in your system where load is unevenly distributed.  Results include:

* per-volume MB/s read and written
* per-brick MB/s read and written
* per-volume per-FOP latency stats + call rates
* per-brick per-FOP (File OPeration) latency stats + call rate

It consists of:

* gvp.sh: a bash script which runs the above command periodically for a number
of samples, storing the results in a file.
* extract_glvolprof.py: a python script that takes that output file
and massages it into a form that can be used for visualization & analysis 

One component of this directory is an HTML file that can be viewed in a
browser. The other is a bunch of CSV files containing the
data. These files can also be used with a spreadsheet application if
desired, to produce graphs that way

Copy the scripts to some Gluster server in your cluster, (i.e. where you can run gluster volume profile command) and run the gvp.sh script. As an illustration, let's say we want to run it every 60 seconds and 10 iterations
(10 minutes of operation) - in practice, you might want to
do that periodically, perhaps in a cron job, in order to see the behavior
of the cluster over time.

\# ./gvp.sh 60 10

Then run the extract script
on that output file:

\# python extract-gl-client-prof.py gvp.log



# client-side profiling

Client-side profiling allows you to see activity as close to the application as possible, at the top of the Gluster translator stack.  This is particularly useful for identifying response time problems for the application  related to Gluster activity.  For example, Gluster replication causes a single application WRITE FOP to be transformed into multiple WRITE FOPs at the bricks within the volume where the file data resides.  The response time for the application's WRITE request may be significantly different from the brick-level WRITE FOP latencies, because it incorporates the network response time and cannot complete before the brick-level WRITE FOPs complete.

Copy the scripts to some directory on your client (i.e. where mountpoint is), and run the gvp-client.sh script. As an illustration, let's say we want to run it every 10 seconds and 12 iterations
(roughly two minutes of operation) - in practice, you might want to
do that periodically, perhaps in a cron job, in order to see the behavior
of the cluster over time.

\# ./gvp-client.sh 10 12

By default, the output file is called <code>gvp.log</code>. Then run the extract script
on that output file:

\# python extract-gl-client-prof.py gvp.log

The output (a bunch of CSV files and an HTML summary page) is placed in
a subdirectory called gvp.log_csvdir.  In order to take advantage of pbench javascript graphing, then
column 1 in the .csv is always the timestamp in milliseconds when
that sample took place.  This can be disabled by defining the environment variable
SKIP_PBENCH_GRAPHING.

To see the graphs, fire up a browser and point it to the URL that the extract
script printed:

