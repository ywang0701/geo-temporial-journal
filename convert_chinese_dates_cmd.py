import sys
import sanmiao


def convert_dates(text):
    """Convert Chinese historical dates in text to Gregorian."""
    result = sanmiao.cjk_date_interpreter(text)
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert_chinese_dates.py \"your Chinese date string here\"")
        print("Example: python convert_chinese_dates.py \"東漢孝獻皇帝劉協建安十八年二月, 乾隆五年六月初十\"")
        sys.exit(1)

    # Join all arguments as input text (allows spaces/quotes)
    input_text = " ".join(sys.argv[1:])

    try:
        conversions = convert_dates(input_text)
        print("Converted dates:")
        print(conversions)
    except Exception as e:
        print(f"Error: {e}")