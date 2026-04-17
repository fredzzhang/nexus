"""S3 bucket monitor that periodically checks for new files and downloads them.

Usage:
    python s3.py --s3-uri s3://my-bucket/path/to/prefix --pattern '*.jpg' --lookback 12
    python s3.py --s3-uri s3://my-bucket --interval 60 --download-dir /tmp/data

All arguments can also be set via environment variables:
    S3_URI, S3_PATTERN, S3_LOOKBACK, DOWNLOAD_DIR, S3_INTERVAL

Fred Zhang <fredzz@amazon.com>
"""

import os
import time
import boto3
import argparse
from fnmatch import fnmatch
from datetime import datetime, timezone, timedelta

DEFAULT_DOWNLOAD_DIR = './downloads'
DEFAULT_INTERVAL = 3
DEFAULT_LOOKBACK = 24
DEFAULT_PATTERN = '*'


def download_new_files(bucket_name, prefix='', pattern=DEFAULT_PATTERN, cutoff_time=None, download_dir=DEFAULT_DOWNLOAD_DIR):
    """Download files from an S3 bucket that match a glob pattern and are newer than the cutoff time.

    Args:
        bucket_name: Name of the S3 bucket.
        prefix: S3 key prefix to filter objects.
        pattern: Glob pattern to match filenames (e.g. '*.jpg', 'image_*').
        cutoff_time: Only download files modified after this datetime (UTC). None downloads all.
        download_dir: Local directory to save downloaded files.

    Returns:
        Number of files downloaded.
    """
    s3 = boto3.client('s3')
    os.makedirs(download_dir, exist_ok=True)

    paginator = s3.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)

    downloaded_count = 0

    for page in pages:
        if 'Contents' not in page:
            continue

        for obj in page['Contents']:
            key = obj['Key']
            last_modified = obj['LastModified']

            if not fnmatch(os.path.basename(key), pattern):
                continue
            if cutoff_time and last_modified <= cutoff_time:
                continue

            local_path = os.path.join(download_dir, os.path.basename(key))
            if os.path.exists(local_path):
                continue

            s3.download_file(bucket_name, key, local_path)
            print(f"Downloaded: {key} -> {local_path}")
            downloaded_count += 1

    return downloaded_count


def parse_s3_uri(s3_uri):
    """Parse an S3 URI (s3://bucket/prefix) into bucket name and prefix."""

    if not s3_uri.startswith('s3://'):
        raise ValueError("S3 URI must start with 's3://'")
    path = s3_uri[5:]
    parts = path.split('/', 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ''
    return bucket, prefix


def monitor_bucket(s3_uri, pattern=DEFAULT_PATTERN, lookback=DEFAULT_LOOKBACK, download_dir=DEFAULT_DOWNLOAD_DIR, interval=DEFAULT_INTERVAL):
    """Continuously monitor an S3 bucket and download new matching files at regular intervals.

    Args:
        s3_uri: S3 URI (s3://bucket/prefix).
        pattern: Glob pattern to match filenames.
        lookback: Only download files created within the last N hours.
        download_dir: Local directory to save downloaded files.
        interval: Seconds between each check.
    """
    bucket_name, prefix = parse_s3_uri(s3_uri)
    print(f"Monitoring s3://{bucket_name}/{prefix}")
    print(f"File pattern: {pattern}")
    print(f"Lookback: {lookback} hours")
    print(f"Download directory: {download_dir}")
    print(f"Check interval: {interval} seconds")

    while True:
        try:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=lookback)
            count = download_new_files(bucket_name, prefix, pattern, cutoff_time, download_dir)
            if count > 0:
                print(f"Downloaded {count} new files at {datetime.now()}")
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Monitor S3 bucket for new files')
    parser.add_argument('--s3-uri', default=os.getenv('S3_URI'), help='S3 URI (s3://bucket/prefix)')
    parser.add_argument('--pattern', default=os.getenv('S3_PATTERN', DEFAULT_PATTERN), help='Glob pattern to match filenames (default: *)')
    parser.add_argument('--lookback', type=float, default=float(os.getenv('S3_LOOKBACK', DEFAULT_LOOKBACK)), help='Download files created within the last N hours (default: 24)')
    parser.add_argument('--download-dir', default=os.getenv('DOWNLOAD_DIR', DEFAULT_DOWNLOAD_DIR), help='Download directory (default: ./downloads)')
    parser.add_argument('--interval', type=int, default=int(os.getenv('S3_INTERVAL', DEFAULT_INTERVAL)), help='Check interval in seconds (default: 300)')

    args = parser.parse_args()

    if not args.s3_uri:
        print("Error: S3 URI required. Use --s3-uri or set S3_URI environment variable.")
        exit(1)

    monitor_bucket(args.s3_uri, args.pattern, args.lookback, args.download_dir, args.interval)
