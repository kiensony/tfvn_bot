import json
import collections
import os

INVALID_WORD_CHARS = set("wzj-")

def normalize_old_tone(s: str) -> str:
    """
    Convert legacy tone placement to modern standard
    """
    replacements = {
        # oa group
        'oà': 'òa', 'oá': 'óa', 'oả': 'ỏa', 'oã': 'õa', 'oạ': 'ọa',

        # oe group
        'oè': 'òe', 'oé': 'óe', 'oẻ': 'ỏe', 'oẽ': 'õe', 'oẹ': 'ọe',

        # ua group
        'uà': 'ùa', 'uá': 'úa', 'uả': 'ủa', 'uã': 'ũa', 'uạ': 'ụa',

        # ưa group
        'ưà': 'ừa', 'ưá': 'ứa', 'ưả': 'ửa', 'ưã': 'ữa', 'ưạ': 'ựa',
        'ườ': 'ường', 'ướ': 'ướ', 'ưở': 'ưởng', 'ưỡ': 'ưỡng', 'ượ': 'ượng',

        # ia / ya group
        'ià': 'ìa', 'iá': 'ía', 'iả': 'ỉa', 'iã': 'ĩa', 'ịa': 'ịa',
        'yà': 'ỳa', 'yá': 'ýa', 'yả': 'ỷa', 'yã': 'ỹa', 'yạ': 'ỵa',

        # uy group
        'uỳ': 'ùy', 'uý': 'úy', 'uỷ': 'ủy', 'uỹ': 'ũy', 'ụy': 'ụy',

        # uô / ô group
        'uồ': 'uồ', 'uố': 'uố', 'uổ': 'uổ', 'uỗ': 'uỗ', 'uộ': 'uộ',
    }

    for old, new in replacements.items():
        s = s.replace(old, new)

    s = s.replace('quì', 'quỳ')
    return s.strip()

def prepare_data():
    input_file = os.path.join(os.path.dirname(__file__), 'words.txt')
    output_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'vietnamese_king_data.json')
    
    results = []
    
    with open(input_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                word = data.get('text', '')
                if not word:
                    continue
                
                word_lower = word.lower()
                if any(char in word_lower for char in INVALID_WORD_CHARS):
                    continue

                standardized = normalize_old_tone(word_lower)
                
                space_count = word_lower.count(' ')
                # Filter for less than 4 words (which means 0, 1, or 2 spaces)
                if space_count >= 3:
                    continue
                
                # Count characters excluding spaces
                char_count = dict(collections.Counter(word_lower.replace(' ', '').replace('-', '')))
                
                results.append({
                    "word": word_lower,
                    "standardize": standardized,
                    "word_len": len(standardized),
                    "space_count": space_count,
                    "count": char_count
                })
            except Exception as e:
                print(f"Error on line: {line.strip()[:50]}... -> {type(e).__name__}: {str(e)}")
                pass
                
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
        
    print(f"Prepared {len(results)} words and saved to {output_file}")

if __name__ == '__main__':
    prepare_data()
