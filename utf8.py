#!/usr/bin/env python3
import re
import sys
import errno
import logging
import argparse
import unicodedata
assert sys.version_info.major >= 3, 'Python 3 required'

DESCRIPTION = """Convert UTF-8 encoded bytes into Unicode characters, or vice versa."""


def make_argparser():
  parser = argparse.ArgumentParser(description=DESCRIPTION)
  parser.add_argument('inputs', nargs='*',
    help='Your characters or bytes.')
  parser.add_argument('-i', '--input-type', choices=('bytes', 'chars',), default='bytes',
    help='Whether the input is UTF-8 encoded bytes, or Unicode characters.')
  parser.add_argument('-o', '--output-type', choices=('bytes', 'chars'), default='chars',
    help='What to convert your input into.')
  parser.add_argument('-I', '--input-format', choices=('hex', 'int', 'str'), default='hex',
    help='The format of the input. "str" means to interpret the input argument as the literal '
         'Unicode characters. For "hex", you can include characters outside [0-9A-F]. They will '
         'be removed. If you are giving "chars" in hex (code points), separate them with spaces or '
         'commas.')
  parser.add_argument('-O', '--output-format', choices=('hex', 'int', 'str', 'desc'), default='desc')
  parser.add_argument('-l', '--log', type=argparse.FileType('w'), default=sys.stderr,
    help='Print log messages to this file instead of to stderr. Warning: Will overwrite the file.')
  parser.add_argument('-q', '--quiet', dest='volume', action='store_const', const=logging.CRITICAL,
    default=logging.WARN)
  parser.add_argument('-v', '--verbose', dest='volume', action='store_const', const=logging.INFO)
  parser.add_argument('-D', '--debug', dest='volume', action='store_const', const=logging.DEBUG)
  return parser


def main(argv):

  parser = make_argparser()
  args = parser.parse_args(argv[1:])

  logging.basicConfig(stream=args.log, level=args.volume, format='%(message)s')
  tone_down_logger()

  # Process format arguments.
  input_format = args.input_format
  if args.output_type == 'bytes' and args.output_format == 'desc':
    # The default output for bytes should be hex.
    output_format = 'hex'
  elif args.output_type == 'bytes' and args.output_format == 'str':
    fail('"str" is an invalid output format for type "bytes".')
  else:
    output_format = args.output_format

  code_points = input_to_code_points(args.inputs, args.input_type, input_format)

  for line in code_points_to_output(code_points, args.output_type, output_format):
    print(line)


def input_to_code_points(input_args, input_type, input_format):
  """Parse input into code points."""
  if input_type == 'bytes':
    bin_input = ''
    for input_str in get_input(input_args, input_format):
      if input_format == 'hex':
        hex_input = clean_up_hex(input_str)
        bin_input += hex_to_binary(hex_input)
      elif input_format == 'int':
        integer = int(input_str)
        bin_input += bin(integer)[2:]
    input_bytes = binary_to_bytes(bin_input)
    for char_bytes in chunk_byte_sequence(input_bytes):
      yield char_bytes_to_code_point(char_bytes)
  elif input_type == 'chars':
    if input_format == 'str':
      for char in get_input(input_args, input_format):
        yield ord(char)
    else:
      for input_str in get_input(input_args, input_format):
        if input_format == 'hex':
          hex_input = clean_up_hex(input_str)
          yield int(hex_input, 16)
        elif input_format == 'int':
          yield int(input_str)


def code_points_to_output(code_points, output_type, output_format):
  """Format code points into the output format.
  Yields a series of lines ready to be printed."""
  if output_type == 'chars':
    if output_format == 'desc':
      for code_point in code_points:
        yield format_code_point_output(code_point)
    elif output_format == 'str':
      output_str = ''
      for code_point in code_points:
        output_str += chr(code_point)
      yield output_str
    else:
      output_strs = []
      for code_point in code_points:
        if output_format == 'hex':
          code_point_hex = hex(code_point)[2:].upper()
          code_point_hex = pad_hex(code_point_hex)
          output_strs.append(code_point_hex)
        elif output_format == 'int':
          output_strs.append(str(code_point))
      yield ' '.join(output_strs)
  elif output_type == 'bytes':
    for code_point in code_points:
      #TODO: Do this encoding manually.
      char = chr(code_point)
      char_bytes = bytes(char, 'utf8')
      output_strs = []
      for byte in char_bytes:
        if output_format == 'hex':
          byte_hex = hex(byte)[2:].upper()
          output_strs.append(byte_hex)
        elif output_format == 'int':
          output_strs.append(str(byte))
      yield ' '.join(output_strs)


def get_input(input_args, format):
  if input_args:
    input_chunks = input_args
  else:
    input_chunks = sys.stdin
  for input_chunk in input_chunks:
    if format == 'str':
      for char in input_chunk:
        yield char
    else:
      for input_str in comma_or_space_split(input_chunk):
        yield input_str


def comma_or_space_split(in_str):
  if ',' in in_str:
    return in_str.split(',')
  else:
    return in_str.split()


def clean_up_hex(hex_input):
  upper_input = hex_input.upper()
  return re.sub(r'[^0-9A-F]+', '', upper_input)


def hex_to_binary(hex_input):
  int_input = int(hex_input, 16)
  bin_input = bin(int_input)[2:]
  return pad_binary(bin_input)


def pad_binary(binary):
  bin_len = len(binary)
  num_bytes = ((bin_len-1) // 8) + 1
  num_bits = num_bytes*8
  pad_bits = num_bits-bin_len
  binary = '0'*pad_bits + binary
  return binary


def binary_to_bytes(binary):
  for start in range(0, len(binary), 8):
    stop = start+8
    yield binary[start:stop]


def chunk_byte_sequence(input_bytes):
  bytes_togo = 0
  char_bytes = []
  for byte in input_bytes:
    char_bytes.append(byte)
    if byte.startswith('0'):
      if bytes_togo > 0:
        logging.warn('Invalid byte sequence (not enough continuation bytes): "{}"'
                     .format(' '.join(bytes_togo)))
      yield char_bytes
      char_bytes = []
    elif byte.startswith('11'):
      if bytes_togo > 0:
        logging.warn('Invalid byte sequence (not enough continuation bytes): "{}"'
                     .format(' '.join(char_bytes)))
        char_bytes = []
        bytes_togo = 0
      match = re.search(r'^(1+)0', byte)
      assert match, byte
      leading_bits = match.group(1)
      bytes_togo = leading_bits.count('1') - 1
    elif byte.startswith('10'):
      if bytes_togo == 0:
        logging.warn('Invalid byte sequence (misplaced continuation byte): "{}"'.format(byte))
        char_bytes = []
      else:
        bytes_togo -= 1
        if bytes_togo == 0:
          if len(char_bytes) > 4:
            logging.warn('Invalid byte sequence (more than 4 bytes): "{}"'
                         .format(' '.join(char_bytes)))
          yield char_bytes
          char_bytes = []
  if len(char_bytes) > 0:
    logging.warn('Invalid byte sequence (not enough continuation bytes): "{}"'
                 .format(' '.join(char_bytes)))


def char_bytes_to_code_point(char_bytes):
  """Turn a byte sequence for a single character into the int for the code point it encodes."""
  code_point_bits = None
  for byte in char_bytes:
    if code_point_bits is None:
      if byte.startswith('0'):
        # ASCII (single-byte) character.
        code_point_bits = byte
        break
      elif byte.startswith('11'):
        # Leading byte of a multibyte sequence.
        code_point_bits = re.sub(r'^1+0', '', byte)
      else:
        logging.warn('Invalid byte sequence: "{}" (error on byte {})'
                     .format(' '.join(char_bytes), byte))
        raise ValueError
    else:
      # Continuation byte of a multibyte sequence.
      assert byte.startswith('10'), byte
      code_point_bits += byte[2:]
  return int(code_point_bits, 2)


def format_code_point_output(code_point_int):
  code_point_hex = hex(code_point_int)[2:].upper()
  code_point_hex = pad_hex(code_point_hex)
  character = chr(code_point_int)
  hex_col = 'U+{}:'.format(code_point_hex)
  return '{:9s} {} ({})'.format(hex_col, character, unicodedata.name(character))


def pad_hex(hex_input, pad_to=None):
  """Pad a hexadecimal string with leading zeros.
  If no pad_to is given, pad to the standard Unicode 4 or 6 digit length."""
  hex_len = len(hex_input)
  if pad_to is None:
    if hex_len <= 4:
      pad_to = 4
    else:
      pad_to = 6
  pad_chars = pad_to - hex_len
  return '0'*pad_chars + hex_input


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
