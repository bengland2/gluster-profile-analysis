#!/bin/bash
# gvp.sh - collect performance data from Gluster about a particular Gluster volume
# usage: 
# bash gvp.sh your-gluster-volume sample-count sample-interval output-file
# output-file is optional, defaults to gvp.log
#
# this version of the script puts the data in pbench format
# for HTML graph generation
#
volume_name=$1
sample_count=$2
sample_interval=$3
outfile=$4
if [ "$sample_interval" = "" ]  ; then
  echo "usage: gvp.sh your-gluster-volume sample-count sample-interval-sec [ output-file ] "
  exit 1
fi
if [ -z "$outfile" ] ; then outfile=gvp.log ; fi

# start up profiling

gluster volume profile $volume_name start
gluster volume profile $volume_name info > /tmp/past

# record a timestamp so we know when the data was collected
# this lets us generate timestamps to put .csv output in pbench format

date +%Y-%m-%d-%H-%M > $outfile
echo "$sample_interval $sample_count" >> $outfile

# generate samples  
for min in `seq 1 $sample_count` ; do
  sleep $sample_interval
  gluster volume profile $volume_name info
done >> $outfile 
gluster volume profile $volume_name stop
echo "output written to $outfile"
