#!/bin/bash
# gvp-client.sh - collect perf data from Gluster for client's usage 
# of Gluster volume from 1 mountpoint
#
# ASSUMPTION: "gluster volume profile your-volume start" has already happened
#
# usage: 
#  chmod u+x gvp-client.sh
#  ./gvp-client.sh your-gluster-volume your-client-mountpoint samples interval [ logfile ]
#
volume_name=$1
mountpoint=$2
sample_count=$3
sample_interval=$4
your_logfile=$5
if [ "$sample_interval" = "" ]  ; then
  echo "usage: gvp-client.sh your-gluster-volume your-client-mountpoint sample-count sample-interval-sec [ your-logfile ]"
  exit 1
fi

sample_cmd="setfattr -n trusted.io-stats-dump -v "

timestamp=`date +%Y-%m-%d-%H-%M`
logfile=/var/tmp/gvp-client-${timestamp}.log
if [ -n "$your_logfile" ] ; then
  logfile="$your_logfile"
fi

# make sure not polluted with previous data
rm -f $logfile

# so next sample interval will be $sample_interval
$sample_cmd /var/tmp/gvp.log $mountpoint

for min in `seq 1 $sample_count` ; do
  sleep $sample_interval
  rm -f /var/tmp/gvp.log
  $sample_cmd /var/tmp/gvp.log $mountpoint
  ( date ; cat /var/tmp/gvp.log ) >> $logfile
done
echo "output written to $logfile"

