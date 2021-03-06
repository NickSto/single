#!/usr/bin/env python3
import argparse
import collections
import logging
import os
import re
import shutil
import sys
import time
import requests
from oyaml import oyaml as yaml
try:
  import youtube_dl
except ImportError:
  youtube_dl = None
assert sys.version_info.major >= 3, 'Python 3 required'

API_URL = 'https://www.googleapis.com/youtube/v3/'
DESCRIPTION = """Download videos from a Youtube playlist and save their metadata."""


def make_argparser():
  parser = argparse.ArgumentParser(description=DESCRIPTION)
  parser.add_argument('api_key')
  parser.add_argument('playlist_id',
    help='The playlist id.')
  parser.add_argument('-d', '--download',
    help='Download the videos to this directory too. This will also save metadata on each video '
         'to a text file, one per video.')
  parser.add_argument('-m', '--meta', action='store_true',
    help='Just save metadata file on each video.')
  parser.add_argument('-M', '--max-length', type=int, default=999999,
    help='Don\'t download videos longer than this. Give a time, in minutes. The metadata file '
         'will still be created, though.')
  parser.add_argument('--max-results', type=int, default=50,
    help='The maximum number of videos to fetch from the playlist at a time. It will always fetch '
         'all videos in the playlist, but this changes how big the chunks are.')
  parser.add_argument('-l', '--log', type=argparse.FileType('w'), default=sys.stderr,
    help='Print log messages to this file instead of to stderr. Warning: Will overwrite the file.')
  volume = parser.add_mutually_exclusive_group()
  volume.add_argument('-q', '--quiet', dest='volume', action='store_const', const=logging.CRITICAL,
    default=logging.WARNING)
  volume.add_argument('-v', '--verbose', dest='volume', action='store_const', const=logging.INFO)
  volume.add_argument('--debug', dest='volume', action='store_const', const=logging.DEBUG)
  return parser


def main(argv):

  parser = make_argparser()
  args = parser.parse_args(argv[1:])

  logging.basicConfig(stream=args.log, level=args.volume, format='%(message)s')

  if args.download:
    if youtube_dl is None:
      fail('Error: youtube_dl package required for --download.')
  downloaded = read_downloaded_video_dir(args.download)

  playlist = fetch_playlist(args.api_key, args.playlist_id, args.max_results)

  for playlist_video in playlist['items']:
    index = playlist_video['snippet']['position']+1
    metadata = {'playlist_item':playlist_video}
    video_id = playlist_video['snippet']['resourceId']['videoId']
    video, reason = fetch_video(args.api_key, video_id)
    metadata['video'] = video
    metadata['video_id'] = video_id
    if video is None:
      metadata['missing_reason'] = reason
      metadata['channel'] = None
    else:
      metadata['channel'] = fetch_channel(args.api_key, video['snippet']['channelId'])
    print(format_metadata_human(index, metadata))
    if args.download:
      #TODO: Allow skipping if the video was added to the playlist very recently.
      #      The video added date is in playlist['items'][i]['snippet']['publishedAt'].
      errors = []
      filename = None
      skip_download = False
      if video_id in downloaded:
        video_data = downloaded[video_id]
        move_files(downloaded, video_id, index)
        video_data['verified'] = True
        if 'file' in video_data or video_data.get('downloaded'):
          skip_download = True
          filename = video_data.get('file')
      if skip_download:
        logging.warning('Video already downloaded. Skipping..')
      elif args.meta:
        pass
      elif video is None:
        logging.warning('Video not found. Skipping download..')
      elif parse_duration(video['contentDetails']['duration']) > args.max_length*60:
        logging.warning('Video too long to be downloaded. Skipping..')
      else:
        logging.warning('Downloading..')
        filename, errors = download_video(video_id, args.download, prefix='{} - '.format(index))
      save_metadata(args.download, index, metadata, filename, errors)
    print()

  if args.download:
    trash_dir = os.path.join(args.download, 'trash')
    for video_id, video_data in downloaded.items():
      if not video_data['verified']:
        if not os.path.isdir(trash_dir):
          os.makedirs(trash_dir)
        logging.warning('Video {} does not seem to be in the playlist anymore. Moving to {}..'
                        .format(video_id, trash_dir))
        if 'file' in video_data:
          path = os.path.join(video_data['dir'], video_data['file'])
          shutil.move(path, os.path.join(trash_dir, video_data['file']))
        if 'meta' in video_data:
          path = os.path.join(video_data['dir'], video_data['meta'])
          shutil.move(path, os.path.join(trash_dir, video_data['meta']))


def read_downloaded_video_dir(dirpath):
  """Find existing video and metadata files previously downloaded by this script."""
  videos = {}
  for filename in os.listdir(dirpath):
    fields = filename.split('.')
    if filename.endswith('.metadata.yaml') and len(fields) == 4:
      # Read metadata file.
      try:
        index = int(fields[0])
      except ValueError:
        continue
      with open(os.path.join(dirpath, filename), 'r') as meta_file:
        metadata = yaml.safe_load(meta_file)
      video_id = fields[1]
      video_data = videos.get(video_id, {})
      video_data['index'] = index
      video_data['meta'] = filename
      if metadata.get('downloaded'):
        video_data['downloaded'] = True
      videos[video_id] = video_data
    else:
      # Read video filename.
      video_id = parse_video_id(filename, strict=True)
      if video_id is None:
        continue
      video_id = fields[-2][-12:-1]
      fields = filename.split(' - ')
      index = int(fields[0])
      video_data = videos.get(video_id, {})
      video_data['index'] = index
      video_data['file'] = filename
      video_data['name'] = ' - '.join(fields[1:])
      videos[video_id] = video_data
  for video_id, video_data in videos.items():
    video_data['dir'] = dirpath
    # verified: Whether this has been verified to still be in the playlist (deafault to False).
    video_data['verified'] = False
  return videos


def read_existing_video_dir(dirpath):
  """Search for any video files that include their video id in the filename."""
  videos = {}
  for dirpath, dirnames, filenames in os.walk(dirpath):
    for filename in filenames:
      video_id = parse_video_id(filename, strict=False)
      if video_id is not None:
        videos[video_id] = {'dir':dirpath, 'file':filename}
  return videos


def parse_video_id(filename, strict=True):
  """Try to retrieve a video id from a filename."""
  if strict:
    # The id must be within ' [id XXXXXXXXXXX]' at the end of the filename (right before the
    # file extension).
    fields = filename.split('.')
    if len(fields) < 2 or not fields[-2].endswith(']') or fields[-2][-17:-12] != ' [id ':
      return None
    video_id = fields[-2][-12:-1]
    if re.search(r'[^0-9A-Za-z_-]', video_id):
      return None
  else:
    i = filename.find(' [id ')
    if i != -1 and len(filename) > i+16 and filename[i+16] == ']':
      # Find a ' [id XXXXXXXXXXX]' anywhere in the filename?
      video_id = filename[i+5:i+16]
      if re.search(r'[^0-9A-Za-z_-]', video_id):
        return None
    else:
      return None
  return video_id


def move_files(downloaded, video_id, index):
  """Check if the current video has already been downloaded, but with a different name, then move it
  to the proper name.
  Do the same with metadata files."""
  if video_id not in downloaded:
    return False
  metadata = downloaded[video_id]
  if index == metadata['index']:
    return False
  logging.warning('Video {} already saved. Renumbering from {} to {}..'
                  .format(video_id, metadata['index'], index))
  # Move the video file.
  if 'file' in metadata:
    old_path = os.path.join(metadata['dir'], metadata['file'])
    new_path = os.path.join(metadata['dir'], '{} - {}'.format(index, metadata['name']))
    check_and_move(old_path, new_path)
  # Move the metadata file.
  if 'meta' in metadata:
    old_path = os.path.join(metadata['dir'], metadata['meta'])
    new_path = os.path.join(metadata['dir'], '{}.{}.metadata.yaml'.format(index, video_id))
    check_and_move(old_path, new_path)
  return True


def check_and_move(src, dst):
  if os.path.exists(dst):
    fail('Error: Cannot move file {!r}. Destination {!r} already exists.'.format(src, dst))
  try:
    shutil.move(src, dst)
  except FileNotFoundError:
    fail('Error: Cannot move file {!r} (file not found).'.format(src))
  except PermissionError as error:
    fail('Error: Cannot move file {!r}. {}: {}'.format(src, type(error).__name__, error.args[1]))


def format_metadata_human(index, metadata):
  if metadata['video'] is None:
    return '{}: [{missing_reason}]\nhttps://www.youtube.com/watch?v={video_id}'.format(index, **metadata)
  else:
    return """{:<3s} {title}
Channel: {channel_title} - https://www.youtube.com/channel/{channel_id}
Upload date: {upload_date}
https://www.youtube.com/watch?v={video_id}""".format(
      str(index)+':',
      title=metadata['video']['snippet']['title'],
      channel_title=metadata['channel']['snippet']['title'],
      channel_id=metadata['channel']['id'],
      upload_date=metadata['video']['snippet']['publishedAt'][:10],
      video_id=metadata['video_id']
    )


def format_metadata_yaml(metadata, got_file, errors=()):
  output = collections.OrderedDict()
  output['url'] = 'https://www.youtube.com/watch?v='+metadata['video_id']
  if metadata['video'] is None:
    output[metadata['missing_reason']] = True
  else:
    output['title'] = metadata['video']['snippet']['title']
    output['channel'] = metadata['channel']['snippet']['title']
    output['channelUrl'] = 'https://www.youtube.com/channel/'+metadata['channel']['id']
    output['uploaded'] = metadata['video']['snippet']['publishedAt'][:10]
    output['addedToPlaylist'] = metadata['playlist_item']['snippet']['publishedAt'][:10]
    output['length'] = parse_duration(metadata['video']['contentDetails']['duration'])
    # Do some cleaning of the description string to let it appear as a clean literal in the yaml
    # file. Human readability is more important than 100% fidelity here, since we're just trying to
    # archive the description to give some sense of the context.
    # Note: PyYAML will output strings with newlines as literal line breaks (readable), unless there
    # is whitespace at the start or end of any line in the string.
    desc_lines = metadata['video']['snippet']['description'].splitlines()
    output['description'] = '\n'.join([line.strip() for line in desc_lines])
    for error in set(errors):
      if error != 'exists':
        output[error] = True
    if got_file:
      output['downloaded'] = True
  return yaml.dump(output, default_flow_style=False)


def save_metadata(dest_dir, index, metadata, filename, errors=()):
  if filename is None:
    got_file = False
  else:
    video_path = os.path.join(dest_dir, filename)
    got_file = os.path.isfile(video_path) and os.path.getsize(video_path) > 0
  meta_path = os.path.join(dest_dir, '{}.{}.metadata.yaml'.format(index, metadata['video_id']))
  if os.path.exists(meta_path):
    logging.warning('Warning: Metadata file {} already exists. Avoiding overwrite..'
                    .format(meta_path))
  with open(meta_path, 'w') as meta_file:
    meta_file.write(format_metadata_yaml(metadata, got_file, errors)+'\n')


def parse_duration(dur_str):
  assert dur_str.startswith('PT'), dur_str
  hours = 0
  minutes = 0
  seconds = 0
  for time_spec in re.findall(r'\d+[HMS]', dur_str):
    if time_spec.endswith('H'):
      hours = int(time_spec[:-1])
    elif time_spec.endswith('M'):
      minutes = int(time_spec[:-1])
    elif time_spec.endswith('S'):
      seconds = int(time_spec[:-1])
  return hours*60*60 + minutes*60 + seconds


##### Begin Youtube API section #####

def fetch_playlist(api_key, playlist_id, max_results=50):
  playlist = None
  params = {
    'playlistId':playlist_id,
    'maxResults':max_results,
    'part':'snippet',
    'key':api_key
  }
  nextPageToken = None
  done = False
  while not done:
    params['pageToken'] = nextPageToken
    data = call_api('playlistItems', params, api_key)
    nextPageToken = data.get('nextPageToken')
    if nextPageToken is None:
      done = True
    if playlist is None:
      playlist = data
    else:
      playlist['items'].extend(data['items'])
  return playlist


def fetch_channel(api_key, channel_id):
  params = {
    'id':channel_id,
    'part':'snippet',
  }
  data = call_api('channels', params, api_key)
  return data['items'][0]


def fetch_video(api_key, video_id):
  params = {
    'id':video_id,
    'part':'snippet,contentDetails'
  }
  data = call_api('videos', params, api_key)
  if data['items']:
    return data['items'][0], None
  elif data['pageInfo']['totalResults'] == 1:
    return None, 'deleted'
  else:
    return None, 'private'


def call_api(api_name, params, api_key):
  our_params = params.copy()
  our_params['key'] = api_key
  response = requests.get(API_URL+api_name, params=our_params)
  if response.status_code != 200:
    error = get_error(response)
    if error:
      fail('Error fetching playlist data. Server message: '+str(error))
    else:
      fail('Error fetching playlist data. Received a {} response.'.format(response.status_code))
  return response.json()


def get_error(response):
  data = response.json()
  if 'error' in data:
    return data['error'].get('message')
  else:
    return None

##### End Youtube API section #####


##### Begin youtube-dl section #####

def download_video(video_id, destination, quality='18', prefix=''):
  filename_template = (prefix+'%(title)s [src %(uploader)s, %(uploader_id)s] '
                     '[posted %(upload_date)s] [id %(id)s].%(ext)s')
  prev_dir = os.getcwd()
  try:
    os.chdir(destination)
    ydl_opts = {
      'format':quality,
      'outtmpl':filename_template,
      'logger':YoutubeDlLogger(),
      #TODO: xattrs
    }
    try:
      call_youtube_dl(video_id, ydl_opts)
    except youtube_dl.utils.DownloadError as error:
      if hasattr(error, 'exc_info'):
        if error.exc_info[1].args[0] == 'requested format not available':
          del ydl_opts['format']
          call_youtube_dl(video_id, ydl_opts)
    filename = get_video_filename(DownloadMetadata, video_id)
    if filename is not None:
      set_date_modified(filename, DownloadMetadata['errors'])
    return filename, DownloadMetadata['errors']
  finally:
    os.chdir(prev_dir)


def call_youtube_dl(video_id, ydl_opts):
  DownloadMetadata['titles'] = []
  DownloadMetadata['merged'] = None
  DownloadMetadata['errors'] = []
  with youtube_dl.YoutubeDL(ydl_opts) as ydl:
    ydl.download(['https://www.youtube.com/watch?v={}'.format(video_id)])


def get_video_filename(download_metadata, video_id):
  if download_metadata['merged']:
    logging.debug('Video created from merged video/audio.')
    filename = download_metadata['merged']
  elif len(download_metadata['titles']) == 1:
    filename = download_metadata['titles'][0]
  elif download_metadata['errors']:
    for error in download_metadata['errors']:
      if error == 'blocked':
        logging.error('Error: Video {} blocked.'.format(video_id))
      elif error == 'restricted':
        logging.warning('Error: Video {} restricted and unavailable.'.format(video_id))
      elif error == 'unavailable':
        logging.warning('Error: Video {} unavailable.'.format(video_id))
      elif error == 'exists':
        logging.warning('Video already downloaded. Skipping..')
    if not download_metadata['errors']:
      logging.error('Error: Video {} not downloaded.'.format(video_id))
    filename = None
  elif len(download_metadata['titles']) == 0:
    fail('Error: failed to determine filename of downloaded video {}'.format(video_id))
  elif len(download_metadata['titles']) > 1:
    fail('Error: found multiple potential filenames for downloaded video {}:\n{}'
         .format(video_id, '\n'.join(download_metadata['titles'])))
  return filename


def set_date_modified(path, errors):
  now = time.time()
  try:
    os.utime(path, (now, now))
  except FileNotFoundError:
    if not errors:
      fail('Error: Downloaded video {}, but downloaded file not found.'.format(path))


# Define global dict to workaround problem that some data is only available from log messages that
# can only be obtained by intercepting in a hook (no other way to return the data).
DownloadMetadata = {'titles':[], 'merged':None, 'errors':[]}

class YoutubeDlLogger(object):
  def debug(self, message):
    # Ignore standard messages.
    if message.startswith('[youtube]'):
      if (message.endswith(': Downloading webpage') or
          message.endswith(': Downloading video info webpage') or
          message.endswith(': Downloading MPD manifest')):
        return
    elif message.startswith('[dashsegments] Total fragments: '):
      return
    elif message.startswith('\r\x1b[K[download]'):
      if ' ETA ' in message[-20:]:
        return
    elif message.startswith('Deleting original file '):
      return
    # Extract video title info from log messages.
    if message.startswith('[download]'):
      if message[10:24] == ' Destination: ':
        DownloadMetadata['titles'].append(message[24:])
        return
      elif (message.endswith('has already been downloaded and merged') or
          message.endswith('has already been downloaded')):
        DownloadMetadata['errors'].append('exists')
    elif message.startswith('[ffmpeg] Merging formats into '):
      DownloadMetadata['merged'] = message[31:-1]
      return
    logging.info(message)
  def info(self, message):
    logging.info(message)
  def warning(self, message):
    logging.warning(message)
  def error(self, message):
    #TODO: Blocked videos seem to list that fact in
    #      video['contentDetails']['regionRestriction']['blocked'] (it's a list of countries it's
    #      blocked in). Could just check for 'US' in that list. Note: according to the documentation,
    #      an empty list means it's not blocked anywhere. There's also an 'allowed' list that may
    #      be there instead. If it is, it's viewable everywhere not on that list (even if it's empty).
    #      See https://developers.google.com/youtube/v3/docs/videos#contentDetails.regionRestriction
    if message.startswith('\x1b[0;31mERROR:\x1b[0m'):
      if (message[17:51] == ' This video contains content from ' and (
            message.endswith('. It is not available.') or
            message.endswith('. It is not available in your country.') or
            message.endswith(', who has blocked it on copyright grounds.') or
            message.endswith(', who has blocked it in your country on copyright grounds.'))):
        DownloadMetadata['errors'].append('blocked')
        return
      elif message[17:] == ' The uploader has not made this video available.':
        DownloadMetadata['errors'].append('restricted')
        return
      elif message[17:] == ' This video is not available.':
        DownloadMetadata['errors'].append('unavailable')
        return
    logging.error(message)
  def critical(self, message):
    logging.critical(message)

##### End youtube-dl section #####


def fail(message):
  logging.critical(message)
  if __name__ == '__main__':
    sys.exit(1)
  else:
    raise Exception('Unrecoverable error')


if __name__ == '__main__':
  try:
    sys.exit(main(sys.argv))
  except BrokenPipeError:
    pass
