import argparse
from opencc import OpenCC


def convert_file(input_path, output_path, config='t2s'):
    converter = OpenCC(config)  # 't2s' or 't2sp'

    with open(input_path, 'r', encoding='utf-8') as f:
        text = f.read()

    converted = converter.convert(text)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(converted)

    print(f"Converted {input_path} → {output_path} (using {config})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Traditional Chinese text file to Simplified Chinese")
    parser.add_argument("input_file", help="Input file path (Traditional Chinese)")
    parser.add_argument("output_file", help="Output file path (Simplified Chinese)")
    parser.add_argument("--config", choices=['t2s', 't2sp'], default='t2s',
                        help="'t2s': basic conversion, 't2sp': Taiwan → Mainland phrases (recommended)")

    args = parser.parse_args()
    convert_file(args.input_file, args.output_file, args.config)