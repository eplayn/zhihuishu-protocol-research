#!/usr/bin/env node
/**
 * 多实例 obfuscator.io 反混淆器
 * ================================================================
 *
 * 一个 bundle 里可能有【多个混淆器实例】，各自有独立的字符串数组 + 索引偏移。
 * 必须分别提取、分别建立「别名 → (数组, 偏移)」映射，否则索引会串。
 *
 * 核心难点是【字符串数组旋转】（obfuscator.io 的自防御特性）：
 *   - 数组被整体旋转了 N 位，但代码里的索引仍是旋转前的
 *   - 不处理的话，所有字符串都是错的，且【不会报错】—— 症状极隐蔽
 *
 * 本工具两种求解方式：
 *   1) 优先：模拟 obfuscator 生成的自保护校验和表达式，逐 N 试解
 *   2) 兜底：手动指定 ROT（配合 anchor.mjs 用锚点反推）
 *
 * 用法:
 *   node deobf.mjs <input.js> [output.js]
 *   ROT=286 node deobf.mjs <input.js> [output.js]      # 手动指定旋转量
 *
 * 配套:
 *   node anchor.mjs <file.js> <偏移hex> <idxhex:期望字符串> ...
 *       ↑ 用已知锚点反推旋转量
 */
import fs from 'node:fs';
import path from 'node:path';

const IN = process.argv[2];
const OUT = process.argv[3] ||
  (IN ? path.join(path.dirname(IN), path.basename(IN).replace(/\.js$/, '') + '.deobf.js') : null);

if (!IN) {
  console.error('用法: node deobf.mjs <input.js> [output.js]');
  console.error('      ROT=<n> node deobf.mjs <input.js> [output.js]');
  process.exit(1);
}
if (!fs.existsSync(IN)) {
  console.error('文件不存在:', IN);
  process.exit(1);
}

const src = fs.readFileSync(IN, 'utf8');
console.log('输入:', IN, `(${(src.length / 1024 / 1024).toFixed(2)} MB)`);

// ---------------------------------------------------------------
//  解析 JS 字符串数组字面量（支持 \x \u 转义）
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

// ---------------------------------------------------------------
//  1. 找出所有解码器实例
//     形如: function NAME(a, b) { var x = arrFn(); return NAME = function(y) { y = y - OFFSET; ... } }
// ---------------------------------------------------------------
const decoders = [];
{
  const re = /function\s+(\w+)\s*\(\s*\w+\s*,\s*\w+\s*\)\s*\{\s*var\s+\w+\s*=\s*(\w+)\s*\(\s*\)\s*;\s*return\s+(\w+)\s*=\s*function\s*\([^)]*\)\s*\{\s*\w+\s*=\s*\w+\s*-\s*(0x[0-9a-fA-F]+)\s*;/g;
  let m;
  while ((m = re.exec(src))) {
    if (m[1] !== m[3]) continue; // 必须是自我重赋值
    decoders.push({ name: m[1], arrFn: m[2], offset: parseInt(m[4], 16), pos: m.index });
  }
}
console.log(`\n发现 ${decoders.length} 个解码器实例`);
for (const d of decoders) {
  console.log(`  ${d.name}  arrFn=${d.arrFn}  offset=0x${d.offset.toString(16)} (${d.offset})  @${d.pos}`);
}
if (!decoders.length) {
  console.error('未发现 obfuscator.io 解码器实例，可能不是该混淆器的产物。');
  process.exit(1);
}

// ---------------------------------------------------------------
//  2. 每个实例：提取字符串数组 + 探测旋转量
// ---------------------------------------------------------------
for (const d of decoders) {
  const afIdx = src.indexOf('function ' + d.arrFn + '()');
  if (afIdx < 0) { d.arr = []; continue; }
  const open = src.indexOf('[', afIdx);
  d.arr = parseJsArray(src, open);
  d.rotated = 0;

  // --- 方式 1：模拟自保护校验和表达式 ---
  const argRe = new RegExp('\\}\\s*\\(\\s*' + d.arrFn + '\\s*,\\s*(0x[0-9a-fA-F]+|[0-9]+)\\s*\\)');
  const mArg = argRe.exec(src);
  let solved = -3; // -3 = 未尝试成功
  if (mArg) {
    const target = parseInt(mArg[1], 16);
    const seg = src.slice(Math.max(0, mArg.index - 3000), mArg.index);
    const mRot = /while\s*\(\s*!!\[\]\s*\)\s*\{\s*try\s*\{([\s\S]{0,1500}?)\}\s*catch/i.exec(seg);
    if (mRot) {
      const mv = /var\s+(\w+)\s*=\s*([\s\S]*?);\s*if\s*\(\s*\1\s*===\s*(\w+)\s*\)/.exec(mRot[1]);
      if (mv) {
        const usedFn = /var\s+(\w+)\s*=\s*(\w+)\s*,/.exec(mRot[1])?.[2] || d.name;
        const body = mv[2].replace(new RegExp('\\b' + usedFn + '\\s*\\(', 'g'), '__fn(');
        try {
          const compute = new Function('__fn', 'parseInt', 'return (' + body + ');');
          for (let n = 0; n < d.arr.length; n++) {
            const fn = (idx) => d.arr[(idx - d.offset + n) % d.arr.length];
            let v;
            try { v = compute(fn, parseInt); } catch { v = NaN; }
            if (v === target) { solved = n; break; }
          }
        } catch { solved = -2; }
      }
    }
  }
  d.rotated = solved;

  // --- 方式 2：解不出来时给出诊断信息，提示用 anchor.mjs 手工反推 ---
  if (solved < 0) {
    const refRe = new RegExp(d.name + '\\s*\\(\\s*(0x[0-9a-fA-F]+|\\d+)\\s*[,)]', 'g');
    let mn = Infinity, mx = -Infinity, m;
    while ((m = refRe.exec(src))) {
      const v = parseInt(m[1], 16);
      if (v < mn) mn = v;
      if (v > mx) mx = v;
    }
    console.log(`    （校验和未解出，引用索引范围 0x${mn.toString(16)}..0x${mx.toString(16)}，`
      + `数组覆盖 0x${d.offset.toString(16)}..0x${(d.offset + d.arr.length - 1).toString(16)}）`);
    console.log('      → 请用 anchor.mjs 反推旋转量，再用 ROT=<n> 重跑本工具');
  }

  const status = d.rotated >= 0
    ? `✅ ${d.rotated} 位`
    : (d.rotated === -2 ? '❌ 表达式构造失败' : '❌ 校验和未匹配');
  console.log(`  → ${d.arr.length} 个字符串，旋转探测: ${status}`);
  if (d.rotated < 0) d.rotated = 0;
}

// ---------------------------------------------------------------
//  3. 每个实例独立求别名闭包（同名跨实例要分开）
// ---------------------------------------------------------------
for (const d of decoders) {
  d.aliases = new Set([d.name]);
  for (let round = 0; round < 15; round++) {
    let changed = false;
    const list = [...d.aliases].map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
    const re = new RegExp(`(\\w+)\\s*=\\s*(?:${list})\\s*(?=[,;)\\]])`, 'g');
    let m;
    while ((m = re.exec(src))) {
      if (!d.aliases.has(m[1])) { d.aliases.add(m[1]); changed = true; }
    }
    if (!changed) break;
  }
}

const owner = new Map();
let conflicts = 0;
for (const d of decoders) {
  for (const a of d.aliases) {
    if (owner.has(a) && owner.get(a) !== d) conflicts++;
    else owner.set(a, d);
  }
}
console.log(`\n别名冲突 ${conflicts} 个（跨实例同名，已按先到先得处理）`);

// ---------------------------------------------------------------
//  4. 还原
// ---------------------------------------------------------------
const ROT_OVERRIDE = process.env.ROT ? parseInt(process.env.ROT, 10) : null;
if (ROT_OVERRIDE !== null) {
  for (const d of decoders) d.rotated = ROT_OVERRIDE;
  console.log(`⚠️  使用手动旋转量 ROT=${ROT_OVERRIDE}`);
}

const alt = [...owner.keys()].map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
const callRe = new RegExp(`\\b(${alt})\\s*\\(\\s*(0x[0-9a-fA-F]+|\\d+)\\s*(?:,([^)]*))?\\)`, 'g');

let hits = 0, misses = 0;
const out = src.replace(callRe, (full, fn, num) => {
  const d = owner.get(fn);
  const raw = (parseInt(num, 16) - d.offset + d.rotated) % d.arr.length;
  const v = d.arr[raw < 0 ? raw + d.arr.length : raw];
  if (typeof v !== 'string') { misses++; return full; }
  hits++;
  return JSON.stringify(v);
});

console.log(`还原: 成功 ${hits} 处, 未解析 ${misses} 处`);
fs.writeFileSync(OUT, out);
console.log('已输出:', OUT, `(${(out.length / 1024 / 1024).toFixed(2)} MB)`);
