#!/usr/bin/env python3
"""帳票用の日本語フォント（LifegenceJP）を Noto Sans CJK JP から作る。

元データ（Ubuntu / Debian の fonts-noto-cjk。SIL Open Font License 1.1）:
    /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
    /usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc
の JP フェイスを取り出し、Shift_JIS（cp932 と JIS の両マッピング）で表せる文字に
サブセット化して japan_compliance/public/fonts/ に書き出す。

- 形式は TrueType（.ttf、glyf アウトライン）。wkhtmltopdf（patched Qt 4.8）の PDF 出力で
  CFF の OTF は扱いが不安定なため、CFF の三次曲線を二次曲線に変換して TTF にする。
- OFL の Reserved Font Name に当たらないよう、フォント名を「LifegenceJP」に改める
  （「Noto」「Source」は使わない）。

使い方（要 fontTools。外部からのダウンロードはしない）:
    python3 scripts/build_fonts.py [--src-dir /usr/share/fonts/opentype/noto]
"""

import argparse
import os
import sys

from fontTools import subset
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection, newTable

FAMILY = "LifegenceJP"
FACES = [
	# (元の TTC, 出力ファイル, スタイル名, usWeightClass)
	("NotoSansCJK-Regular.ttc", "LifegenceJP-Regular.ttf", "Regular", 400),
	("NotoSansCJK-Bold.ttc", "LifegenceJP-Bold.ttf", "Bold", 700),
]
SOURCE_FAMILY = "Noto Sans CJK JP"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "japan_compliance", "public", "fonts")

# cp932 と JIS（shift_jis コーデック）で対応先が異なる文字（〜 U+301C と ～ U+FF5E など）は
# 両方を入れる。帳票で使う記号（〒 ①〜⑳ ㈱ － など）は cp932 の範囲に含まれる。
ENCODINGS = ("cp932", "shift_jis")


def charset():
	"""Shift_JIS で表せる文字のコードポイント（私用領域＝外字を除く）。"""
	chars = set()
	singles = list(range(0x20, 0x7F)) + list(range(0xA1, 0xE0))  # ASCII 印字可能文字＋半角カナ
	leads = list(range(0x81, 0xA0)) + list(range(0xE0, 0xFD))
	trails = list(range(0x40, 0x7F)) + list(range(0x80, 0xFD))
	for enc in ENCODINGS:
		for b in singles:
			chars.add(bytes([b]).decode(enc, errors="ignore"))
		for lead in leads:
			for trail in trails:
				try:
					chars.add(bytes([lead, trail]).decode(enc))
				except UnicodeDecodeError:
					pass
	cps = {ord(c) for c in chars if len(c) == 1}
	cps.update({0x20, 0xA0, 0x3000})  # 空白類
	cps.update({0xA5, 0x203E})  # JIS X 0201 の ¥ と ‾（0x5C・0x7E。コーデックは ASCII の \ と ~ に変換する）
	return sorted(cp for cp in cps if not 0xE000 <= cp <= 0xF8FF)


def find_jp_face(ttc_path):
	for font in TTCollection(ttc_path).fonts:
		if font["name"].getDebugName(1) == SOURCE_FAMILY:
			return font
	sys.exit(f"{ttc_path} に {SOURCE_FAMILY} のフェイスがありません")


def cff_to_glyf(font, max_err=1.0):
	"""CFF（三次曲線）を glyf（二次曲線）に変換する。"""
	glyph_order = font.getGlyphOrder()
	glyph_set = font.getGlyphSet()
	glyphs = {}
	for name in glyph_order:
		pen = TTGlyphPen(glyph_set)
		glyph_set[name].draw(Cu2QuPen(pen, max_err, reverse_direction=True))
		glyphs[name] = pen.glyph()

	glyf = newTable("glyf")
	glyf.glyphOrder = glyph_order
	glyf.glyphs = glyphs
	font["glyf"] = glyf
	font["loca"] = newTable("loca")
	del font["CFF "]
	if "VORG" in font:
		del font["VORG"]

	maxp = font["maxp"]
	maxp.tableVersion = 0x00010000
	for attr in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs",
				 "maxInstructionDefs", "maxStackElements", "maxSizeOfInstructions", "maxComponentElements"):
		setattr(maxp, attr, 0)
	maxp.maxZones = 1
	maxp.maxComponentDepth = 0
	font["head"].glyphDataFormat = 0
	font["head"].indexToLocFormat = 0
	post = font["post"]
	post.formatType = 2.0
	post.extraNames = []
	post.mapping = {}
	post.glyphOrder = glyph_order
	font.sfntVersion = "\x00\x01\x00\x00"
	font.recalcBBoxes = True
	font.recalcTimestamp = False  # 元フォントの日時を保ち、再生成しても同じバイト列にする


def rename(font, style, weight, version):
	"""名前テーブルを LifegenceJP に改める。著作権表示（ID 0）とライセンス（ID 13・14）は残す。"""
	name = font["name"]
	ps_name = f"{FAMILY}-{style}"
	keep = {0, 13, 14}
	name.names = [n for n in name.names if n.nameID in keep]
	values = {
		1: FAMILY,
		2: style,
		3: f"{version};{ps_name}",
		4: f"{FAMILY} {style}",
		5: f"Version {version}",
		6: ps_name,
		10: (f"Subset of {SOURCE_FAMILY} {style} {version} (Shift_JIS repertoire) "
			 "modified by Lifegence Corporation for printing. Not the original Noto font."),
	}
	for nid, value in values.items():
		name.setName(value, nid, 3, 1, 0x409)
		name.setName(value, nid, 1, 0, 0)
	font["OS/2"].usWeightClass = weight
	# Regular / Bold の判別（wkhtmltopdf・Chrome が bold を合成せず Bold フェイスを使うように）
	os2 = font["OS/2"]
	os2.fsSelection = (os2.fsSelection & ~0b1100001) | (0b100000 if style == "Bold" else 0b1000000)
	font["head"].macStyle = 1 if style == "Bold" else 0


def build(src_dir, out_dir):
	unicodes = charset()
	os.makedirs(out_dir, exist_ok=True)
	for src, out, style, weight in FACES:
		font = find_jp_face(os.path.join(src_dir, src))
		version = font["name"].getDebugName(5).split(";")[0].replace("Version ", "")
		options = subset.Options()
		# 既定の機能＋全角・半角の切替。locl は中国語・韓国語の字形を大量に引き込むので除く（日本語は既定の字形）
		options.layout_features = [f for f in options.layout_features if f != "locl"] + ["fwid", "hwid"]
		options.name_IDs = ["*"]
		options.name_languages = ["*"]
		options.notdef_outline = True
		options.hinting = False
		options.desubroutinize = True
		subsetter = subset.Subsetter(options)
		subsetter.populate(unicodes=unicodes)
		subsetter.subset(font)
		cff_to_glyf(font)
		rename(font, style, weight, version)
		path = os.path.join(out_dir, out)
		font.save(path)
		covered = len(font.getBestCmap())
		print(f"{path}: {os.path.getsize(path):,} bytes, {covered} chars, {len(font.getGlyphOrder())} glyphs")
	missing = [f"U+{cp:04X}" for cp in unicodes if cp not in font.getBestCmap()]
	print(f"対象 {len(unicodes)} 文字、元フォントに字形がない文字: {' '.join(missing) or 'なし'}")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--src-dir", default="/usr/share/fonts/opentype/noto")
	parser.add_argument("--out-dir", default=os.path.normpath(OUT_DIR))
	args = parser.parse_args()
	build(args.src_dir, args.out_dir)
