#!/usr/bin/env python
import sys
import argparse
import munger
OPT_DEFAULTS = {}
USAGE = """cat file.txt | %(prog)s [options]
       %(prog)s [options] file1.txt [file2.txt [file3.txt [...]]]"""
DESCRIPTION = """Remove duplicate lines from a set of files. It's "sort | uniq",
but it preserves the order of your lines. Plus other tricks."""
EPILOG = """Caution: It holds the entire dataset in memory, as a list."""

def main():

  parser = argparse.ArgumentParser(usage=USAGE, description=DESCRIPTION,
    epilog=EPILOG)
  parser.set_defaults(**OPT_DEFAULTS)
  parser.add_argument('filenames', nargs='*', metavar='file.txt',
    help='Input text file.')
  parser.add_argument('-f', '--field', type=int,
    help='Only consider this (whitespace-delimited) field when determining '
      'duplicate lines. If multiple lines are found with identical values, '
      'only the first is printed. Give a 1-based index.')
  parser.add_argument('-t', '--tab', action='store_true',
    help='Use single tabs instead of whitespace as the field delimiter.')
  parser.add_argument('-q', '--quiet', action='store_true',
    help='Turn off verbose mode.')
  args = parser.parse_args()
  if args.quiet:
    errors = 'silent'
  else:
    errors = 'warn'

  files = []
  for filename in args.filenames:
    if filename == '-':
      files.append(sys.stdin)
    else:
      files.append(open(filename, 'rU'))
  if len(files) == 0:
    files = [sys.stdin]

  seen = set()
  for file_ in files:
    for line in file_:
      value = munger.get_field(line, field=args.field, tab=args.tab,
        errors=errors)
      if not (value in seen or value is None):
        sys.stdout.write(line)
      seen.add(value)

  for file_ in files:
    if file_ is not sys.stdin:
      file_.close()


if __name__ == '__main__':
  main()