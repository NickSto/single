#!/usr/bin/env python3
from __future__ import division
from __future__ import print_function
from __future__ import absolute_import
from __future__ import unicode_literals
import sys
import time
import errno
import logging
import argparse
import urllib.parse
import pinboard
session_reader = __import__('firefox-sessions')

DESCRIPTION = """Bookmark open tabs from a Firefox session with Pinboard."""

# API documentation: https://pinboard.in/api
# Get the auth token from https://pinboard.in/settings/password


def make_argparser():
  parser = argparse.ArgumentParser(description=DESCRIPTION)
  parser.add_argument('session', metavar='backup.session', nargs='?', default=sys.stdin,
    help='Session Manager .session file.')
  parser.add_argument('-t', '--auth-token',
    help='Your Pinboard API authentication token. Available from '
         'https://pinboard.in/settings/password. Not required if only simulating.')
  parser.add_argument('-n', '--simulate', action='store_true',
    help='Only simulate the process, printing the tabs which will be archived but without actually '
         'doing it.')
  parser.add_argument('-w', '--window',
    help='Specify the window to look for tabs in, instead of the default (the biggest window). '
         'Format: WindowNum:NumTabs (e.g. "2:375"). The two, colon-delimited numbers are the '
         'window number, as given by session-manager.py, and the number of tabs in it (to make '
         'sure we\'re talking about the right window).')
  parser.add_argument('-b', '--begin',
    help='The title of the tab to start archiving at (inclusive). You can use just the '
         'beginning of the title, but it must be unique. If not given, will start with the first '
         'tab.')
  parser.add_argument('-e', '--end',
    help='The title of the tab to end archiving at (inclusive). You can use just the beginning of '
         'the title, but it must be unique. If not given, will stop at the last tab.')
  parser.add_argument('-D', '--skip-domains',
    help='Do not archive tabs from these domains. Give as a comma-delimited list, e.g. "tumblr.com,'
         'tastefullyoffensive.com,thefrogman.me,imgur.com,instagram.com,vine.co,pleated-jeans.com"')
  parser.add_argument('-p', '--pause', type=float, default=3.05,
    help='How many seconds to wait in-between each request to the API. The policy in the '
         'documentation (https://pinboard.in/api) is no more than 1 every 3 seconds. You should '
         'get a 429 response if it\'s exceeded. Default: %(default)s.')
  parser.add_argument('-r', '--record', type=argparse.FileType('w'),
    help='Write a record of tabs archived to this file.')
  parser.add_argument('-l', '--log', type=argparse.FileType('w'), default=sys.stderr,
    help='Print log messages to this file instead of to stderr. Warning: Will overwrite the file.')
  parser.add_argument('-q', '--quiet', dest='volume', action='store_const', const=logging.CRITICAL)
  parser.add_argument('-v', '--verbose', dest='volume', action='store_const', const=logging.INFO,
    default=logging.INFO)
  parser.add_argument('-d', '--debug', dest='volume', action='store_const', const=logging.DEBUG)
  return parser


def main(argv):

  parser = make_argparser()
  args = parser.parse_args(argv[1:])

  logging.basicConfig(stream=args.log, level=args.volume, format='%(message)s')
  tone_down_logger()

  if not args.simulate:
    if args.auth_token:
      api = pinboard.ApiInterface(args.auth_token)
    else:
      fail('Error: An --auth-token is required if --simulate is not given.')

  skip_domains = []
  if args.skip_domains:
    skip_domains = args.skip_domains.split(',')

  session = session_reader.read_session_file(args.session)

  if args.window:
    target_window, target_tabs = session_reader.parse_window_spec(args.window)
    window_num = 0
    for window in session['windows']:
      window_num += 1
      if window_num == target_window:
        num_tabs = len(list(session_reader.get_tabs(window)))
        if num_tabs == target_tabs:
          logging.info('Found specified --window (number {}, with {} tabs).'
                       .format(target_window, target_tabs))
          break
        else:
          fail('Error: Window that matches given --window number has wrong number of tabs '
               '(--window gave {}, but window {} has {}).'.format(target_tabs, target_window,
                                                                  num_tabs))
  else:
    window = get_biggest_window(session)
    logging.warn('Found biggest window: {} tabs.'
                 .format(len(list(session_reader.get_tabs(window)))))

  # Go through the tabs, determine which to archive.
  tabs = []
  begin_matches = []
  end_matches = []
  if args.begin:
    archiving = False
  else:
    archiving = True
  begin = False
  end = False
  for tab in session_reader.get_tabs(window):
    # Check when to start.
    if args.begin and tab['title'].startswith(args.begin):
      begin_matches.append(tab['title'])
      begin = True
    else:
      begin = False
    if not archiving:
      if begin:
        archiving = True
      else:
        continue
    # Archive this tab.
    tabs.append(tab)
    # Check when to stop.
    if args.end and tab['title'].startswith(args.end):
      end_matches.append(tab['title'])
      end = True
    else:
      end = False
    if archiving and args.end and end:
      break
  if len(begin_matches) > 1:
    fail('Error: --begin matches multiple tabs:\n'+'\n'.join(begin_matches))
  if len(end_matches) > 1:
    fail('Error: --end matches multiple tabs:\n'+'\n'.join(end_matches))

  logging.warn('Found {} tabs to archive.\n'.format(len(tabs)))

  for tab in tabs:
    if not tab['title']:
      tab['title'] = '.'
    logging.info('\t'+tab['title'][:91])
    if skip_url(tab['url'], skip_domains):
      continue
    if not args.simulate:
      done = api.is_url_bookmarked(tab['url'])
      if done:
        logging.info('Tab already bookmarked. Skipping.')
      time.sleep(args.pause)
      if not done:
        success = api.bookmark_url(tab['url'], tab['title'])
        if success:
          logging.info('success')
        else:
          logging.info('FAILED')
          sys.exit(1)
      if args.record:
        if done:
          result = 'done'
        elif success:
          result = 'bookmarked'
        else:
          result = 'FAILED'
        args.record.write('{}\t{}\t{}\n'.format(result, tab['title'], tab['url']))
      time.sleep(args.pause)


def get_biggest_window(session):
  max_tabs = 0
  biggest_window = None
  for window in session['windows']:
    num_tabs = len(list(session_reader.get_tabs(window)))
    if num_tabs > max_tabs:
      max_tabs = num_tabs
      biggest_window = window
  return biggest_window


def quote(string):
  return urllib.parse.quote_plus(string)


def skip_url(url, skip_domains):
  url_parts = urllib.parse.urlparse(url)
  if url_parts.scheme == 'about':
    logging.info('about: tab. Skipping.')
    return True
  for skip_domain in skip_domains:
    if domain_endswith(url_parts.netloc, skip_domain):
      logging.info('Skipping tab from {}'.format(skip_domain))
      return True
  return False


def domain_endswith(query, ending):
  """Test whether a domain name ends with a certain domain.
  E.g. "www.google.com" ends with "google.com".
  It's basically str.endswith, except "fake-google.com" won't match "google.com"."""
  domain = ''
  subdomains = query.split('.')
  for subdomain in reversed(subdomains):
    if domain:
      domain = subdomain + '.' + domain
    else:
      domain = subdomain
    if domain == ending:
      return True
  return False


def tone_down_logger():
  """Change the logging level names from all-caps to capitalized lowercase.
  E.g. "WARNING" -> "Warning" (turn down the volume a bit in your log files)"""
  for level in (logging.CRITICAL, logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG):
    level_name = logging.getLevelName(level)
    logging.addLevelName(level, level_name.capitalize())


def fail(message):
  logging.critical(message)
  if __name__ == '__main__':
    sys.exit(1)
  else:
    raise Exception('Unrecoverable error')


if __name__ == '__main__':
  try:
    sys.exit(main(sys.argv))
  except IOError as ioe:
    if ioe.errno != errno.EPIPE:
      raise
