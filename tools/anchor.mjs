#!/usr/bin/env node
/**
 * 用锚点反推 obfuscator.io 的字符串数组旋转量
 * ================================================================
 *
 * 【为什么需要】
 * obfuscator.io 的自防御会把字符串数组整体旋转 N 位，
 * 但代码里的索引仍按旋转【前】的位置书写。
 * 不还原旋转量的话，反混淆后所有字符串都是错的 —— 而且【不会报错】。
 *
 * 【原理】
 * 找到若干「代码索引 → 期望字符串」的已知对应（锚点），
 * 对每个候选旋转量 R 检验是否全部满足。多个锚点一致即为强证据。
 *
 * 【怎么找锚点】
 * 在混淆代码里找你觉得「一定是什么」的字符串索引，例如：
 *   - 明显的接口路径（/learning/queryCourse）
 *   - 明显的字段名（secretStr、dateFormate）
 *   - 报错文案
 * 从上下文能推断出索引处【应该】是什么，就能当锚点。
 *
 * 用法:
 *   node anchor.mjs <file.js> <偏移hex> <索引hex>:<期望字符串> [更多锚点...]
 *
 * 示例:
 *   node anchor.mjs app.js 1ce 1e3:queryCourse 1df:videolist
 *
 * 输出里若显示「✅ 旋转量: 286」，则：
 *   ROT=286 node deobf.mjs app.js
 */
import fs from 'node:fs';

const FILE = process.argv[2];
const OFF = process.argv[3] ? parseInt(process.argv[3], 16) : NaN;
const ANCHORS = process.argv.slice(4).map((s) => {
  const i = s.indexOf(':');
  return [parseInt(s.slice(0, i), 16), s.slice(i + 1)];
});

if (!FILE || Number.isNaN(OFF) || !ANCHORS.length) {
  console.error('用法: node anchor.mjs <file.js> <偏移hex> <索引hex>:<期望字符串> [更多...]');
  console.error('示例: node anchor.mjs app.js 1ce 1e3:queryCourse 1df:videolist');
  process.exit(1);
}
if (!fs.existsSync(FILE)) {
  console.error('文件不存在:', FILE);
  process.exit(1);
}

const src = fs.readFileSync(FILE, 'utf8');

// ---------------------------------------------------------------
//  解析 JS 字符串数组字面量
// ---------------------------------------------------------------
function parseJsArray(s, start) {
  const out = [];
  let i = start + 1;
  const esc = {
    n: '\n', r: '\r', t: '\t', b: '\b', f: '\f', v: '\v',
    '0': '\0', '\\': '\\', "'": "'", '"': '"', '`': '`',
  };
  while (i < s.length) {
    while (i < s.length && /[\s,]/.test(s[i])) i++;
    if (s[i] === ']') break;
    const q = s[i];
    if (q !== "'" && q !== '"') { i++; continue; }
    i++;
    let str = '';
    while (i < s.length && s[i] !== q) {
      if (s[i] === '\\') {
        const n = s[i + 1];
        if (n === 'x') { str += String.fromCharCode(parseInt(s.substr(i + 2, 2), 16)); i += 4; continue; }
        if (n === 'u') { str += String.fromCharCode(parseInt(s.substr(i + 2, 4), 16)); i += 6; continue; }
        str += (esc[n] !== undefined ? esc[n] : n);
        i += 2;
        continue;
      }
      str += s[i];
      i++;
    }
    i++;
    out.push(str);
  }
  return out;
}

// 定位解码器与它的字符串数组
const m = /function\s+(\w+)\s*\(\s*\w+\s*,\s*\w+\s*\)\s*\{\s*var\s+\w+\s*=\s*(\w+)\s*\(\s*\)\s*;/.exec(src);
if (!m) {
  console.error('未发现 obfuscator.io 解码器，可能不是该混淆器的产物。');
  process.exit(1);
}
const arrFn = m[2];
const arr = parseJsArray(src, src.indexOf('[', src.indexOf('function ' + arrFn + '()')));
console.log(`解码器: ${m[1]}   数组函数: ${arrFn}   数组长度: ${arr.length}   偏移: 0x${OFF.toString(16)}`);
console.log('');

// ---------------------------------------------------------------
//  对每个锚点求「数组实际位置 - 原始索引」的差
//  所有锚点的差一致，该差值即为旋转量
// ---------------------------------------------------------------
const diffs = [];
for (const [idx, want] of ANCHORS) {
  const raw = idx - OFF;
  const pos = arr.indexOf(want);
  const diff = pos - raw;
  const cur = arr[((raw % arr.length) + arr.length) % arr.length];
  console.log(`  索引 0x${idx.toString(16)}  raw=${raw}`);
  console.log(`    当前内容: ${JSON.stringify(String(cur).slice(0, 48))}`);
  console.log(`    期望内容: ${JSON.stringify(want)}`);
  console.log(`    在数组中位置: ${pos}   差值: ${diff}`);
  diffs.push(diff);
}

const uniq = [...new Set(diffs)];
console.log('');
if (uniq.length === 1) {
  console.log(`✅ 推断旋转量: ${uniq[0]}   （${ANCHORS.length}/${ANCHORS.length} 个锚点一致）`);
  console.log('');
  console.log(`  下一步:  ROT=${uniq[0]} node deobf.mjs "${FILE}"`);
} else if (uniq.includes(-1)) {
  console.log(`❌ 有锚点未在数组中找到（差值 -1），请检查「期望字符串」是否写对了`);
  console.log(`   各锚点差值: ${JSON.stringify(diffs)}`);
} else {
  console.log(`⚠️  锚点不一致: ${JSON.stringify(uniq)}`);
  console.log('   说明锚点中有写错的，或该文件有多个混淆器实例（用 deobf.mjs 分别处理）');
}
