"use strict";

(function () {
  const ECC_PER_BLOCK = {
    L: [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18],
    M: [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26],
    Q: [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24],
    H: [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28],
  };
  const NUM_BLOCKS = {
    L: [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4],
    M: [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5],
    Q: [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8],
    H: [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8],
  };
  const FORMAT_BITS = { L: 1, M: 0, Q: 3, H: 2 };
  const MAX_VERSION = 10;

  function rawDataModules(ver) {
    let result = (16 * ver + 128) * ver + 64;
    if (ver >= 2) {
      const numAlign = Math.floor(ver / 7) + 2;
      result -= (25 * numAlign - 10) * numAlign - 55;
      if (ver >= 7) result -= 36;
    }
    return result;
  }

  function dataCodewords(ver, ecl) {
    return Math.floor(rawDataModules(ver) / 8) - ECC_PER_BLOCK[ecl][ver] * NUM_BLOCKS[ecl][ver];
  }

  function alignmentPositions(ver, size) {
    if (ver === 1) return [];
    const numAlign = Math.floor(ver / 7) + 2;
    const step = Math.ceil((ver * 4 + 4) / (numAlign * 2 - 2)) * 2;
    const result = [6];
    for (let pos = size - 7; result.length < numAlign; pos -= step) result.splice(1, 0, pos);
    return result;
  }

  function gfMul(x, y) {
    let z = 0;
    for (let i = 7; i >= 0; i--) {
      z = (z << 1) ^ ((z >>> 7) * 0x11d);
      z ^= ((y >>> i) & 1) * x;
    }
    return z & 0xff;
  }

  function rsDivisor(degree) {
    const result = new Array(degree).fill(0);
    result[degree - 1] = 1;
    let root = 1;
    for (let i = 0; i < degree; i++) {
      for (let j = 0; j < result.length; j++) {
        result[j] = gfMul(result[j], root);
        if (j + 1 < result.length) result[j] ^= result[j + 1];
      }
      root = gfMul(root, 0x02);
    }
    return result;
  }

  function rsRemainder(data, divisor) {
    const result = new Array(divisor.length).fill(0);
    for (const b of data) {
      const factor = b ^ result.shift();
      result.push(0);
      divisor.forEach((coef, i) => { result[i] ^= gfMul(coef, factor); });
    }
    return result;
  }

  function utf8Bytes(text) {
    return Array.from(new TextEncoder().encode(text));
  }

  function encodeData(bytes, ver, ecl) {
    const bits = [];
    const push = (value, len) => { for (let i = len - 1; i >= 0; i--) bits.push((value >>> i) & 1); };
    push(0x4, 4);
    push(bytes.length, ver <= 9 ? 8 : 16);
    bytes.forEach((b) => push(b, 8));
    const capacity = dataCodewords(ver, ecl) * 8;
    push(0, Math.min(4, capacity - bits.length));
    push(0, (8 - (bits.length % 8)) % 8);
    const out = [];
    for (let i = 0; i < bits.length; i += 8) {
      let byte = 0;
      for (let j = 0; j < 8; j++) byte = (byte << 1) | bits[i + j];
      out.push(byte);
    }
    for (let pad = 0xec; out.length < capacity / 8; pad ^= 0xec ^ 0x11) out.push(pad);
    return out;
  }

  function addEccAndInterleave(data, ver, ecl) {
    const numBlocks = NUM_BLOCKS[ecl][ver];
    const eccLen = ECC_PER_BLOCK[ecl][ver];
    const rawCodewords = Math.floor(rawDataModules(ver) / 8);
    const numShort = numBlocks - (rawCodewords % numBlocks);
    const shortLen = Math.floor(rawCodewords / numBlocks);
    const divisor = rsDivisor(eccLen);
    const blocks = [];
    for (let i = 0, k = 0; i < numBlocks; i++) {
      const dat = data.slice(k, k + shortLen - eccLen + (i < numShort ? 0 : 1));
      k += dat.length;
      const ecc = rsRemainder(dat, divisor);
      if (i < numShort) dat.push(0);
      blocks.push(dat.concat(ecc));
    }
    const result = [];
    for (let i = 0; i < blocks[0].length; i++) {
      blocks.forEach((block, j) => {
        if (i !== shortLen - eccLen || j >= numShort) result.push(block[i]);
      });
    }
    return result;
  }

  const MASKS = [
    (x, y) => (x + y) % 2 === 0,
    (x, y) => y % 2 === 0,
    (x) => x % 3 === 0,
    (x, y) => (x + y) % 3 === 0,
    (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
    (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
    (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
    (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
  ];

  function build(ver, ecl, codewords, mask) {
    const size = ver * 4 + 17;
    const modules = Array.from({ length: size }, () => new Array(size).fill(false));
    const isFunction = Array.from({ length: size }, () => new Array(size).fill(false));
    const set = (x, y, dark) => { modules[y][x] = dark; isFunction[y][x] = true; };

    for (let i = 0; i < size; i++) {
      set(6, i, i % 2 === 0);
      set(i, 6, i % 2 === 0);
    }
    const finder = (cx, cy) => {
      for (let dy = -4; dy <= 4; dy++) {
        for (let dx = -4; dx <= 4; dx++) {
          const x = cx + dx; const y = cy + dy;
          if (x < 0 || y < 0 || x >= size || y >= size) continue;
          const dist = Math.max(Math.abs(dx), Math.abs(dy));
          set(x, y, dist !== 2 && dist !== 4);
        }
      }
    };
    finder(3, 3);
    finder(size - 4, 3);
    finder(3, size - 4);
    const align = alignmentPositions(ver, size);
    const n = align.length;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if ((i === 0 && j === 0) || (i === 0 && j === n - 1) || (i === n - 1 && j === 0)) continue;
        for (let dy = -2; dy <= 2; dy++) {
          for (let dx = -2; dx <= 2; dx++) {
            set(align[i] + dx, align[j] + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
          }
        }
      }
    }

    const data = (FORMAT_BITS[ecl] << 3) | mask;
    let rem = data;
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    const fbits = ((data << 10) | rem) ^ 0x5412;
    const bit = (v, i) => ((v >>> i) & 1) !== 0;
    for (let i = 0; i <= 5; i++) set(8, i, bit(fbits, i));
    set(8, 7, bit(fbits, 6));
    set(8, 8, bit(fbits, 7));
    set(7, 8, bit(fbits, 8));
    for (let i = 9; i < 15; i++) set(14 - i, 8, bit(fbits, i));
    for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(fbits, i));
    for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(fbits, i));
    set(8, size - 8, true);

    if (ver >= 7) {
      let r = ver;
      for (let i = 0; i < 12; i++) r = (r << 1) ^ ((r >>> 11) * 0x1f25);
      const vbits = (ver << 12) | r;
      for (let i = 0; i < 18; i++) {
        const a = size - 11 + (i % 3);
        const b = Math.floor(i / 3);
        set(a, b, bit(vbits, i));
        set(b, a, bit(vbits, i));
      }
    }

    let i = 0;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < size; vert++) {
        for (let j = 0; j < 2; j++) {
          const x = right - j;
          const upward = ((right + 1) & 2) === 0;
          const y = upward ? size - 1 - vert : vert;
          if (!isFunction[y][x] && i < codewords.length * 8) {
            modules[y][x] = bit(codewords[i >>> 3], 7 - (i & 7));
            i++;
          }
        }
      }
    }
    const fn = MASKS[mask];
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (!isFunction[y][x] && fn(x, y)) modules[y][x] = !modules[y][x];
      }
    }
    const centres = [];
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if ((i === 0 && j === 0) || (i === 0 && j === n - 1) || (i === n - 1 && j === 0)) continue;
        centres.push([align[i], align[j]]);
      }
    }
    return { size, modules, isFunction, align: centres };
  }

  function penalty(modules) {
    const size = modules.length;
    let score = 0;
    const runs = (get) => {
      for (let a = 0; a < size; a++) {
        let run = 1;
        for (let b = 1; b <= size; b++) {
          if (b < size && get(a, b) === get(a, b - 1)) run++;
          else {
            if (run >= 5) score += 3 + (run - 5);
            run = 1;
          }
        }
      }
    };
    runs((y, x) => modules[y][x]);
    runs((x, y) => modules[y][x]);
    let dark = 0;
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (modules[y][x]) dark++;
        if (x < size - 1 && y < size - 1) {
          const c = modules[y][x];
          if (c === modules[y][x + 1] && c === modules[y + 1][x] && c === modules[y + 1][x + 1]) score += 3;
        }
      }
    }
    const total = size * size;
    score += Math.floor(Math.abs(dark * 20 - total * 10) / total) * 10;
    return score;
  }

  function encode(text, ecl) {
    const level = ECC_PER_BLOCK[ecl] ? ecl : "M";
    const bytes = utf8Bytes(text);
    let ver = 1;
    for (; ver <= MAX_VERSION; ver++) {
      const header = 4 + (ver <= 9 ? 8 : 16);
      if (header + bytes.length * 8 <= dataCodewords(ver, level) * 8) break;
    }
    if (ver > MAX_VERSION) throw new Error("qr_too_long");
    const codewords = addEccAndInterleave(encodeData(bytes, ver, level), ver, level);
    let best = null;
    for (let mask = 0; mask < 8; mask++) {
      const qr = build(ver, level, codewords, mask);
      const score = penalty(qr.modules);
      if (!best || score < best.score) best = Object.assign(qr, { score, version: ver, mask });
    }
    return best;
  }

  function inFinder(x, y, size) {
    return (x < 7 && y < 7) || (x >= size - 7 && y < 7) || (x < 7 && y >= size - 7);
  }

  function svg(text, options) {
    const opts = options || {};
    const qr = encode(text, opts.ecl || "M");
    const size = qr.size;
    const quiet = 4;
    const full = size + quiet * 2;
    const color = opts.color || "#000";
    const bg = opts.background || "#fff";
    const parts = [];
    parts.push(`<rect width="${full}" height="${full}" rx="2.5" fill="${bg}"/>`);

    const inAlign = (x, y) => qr.align.some(([ax, ay]) => Math.abs(x - ax) <= 2 && Math.abs(y - ay) <= 2);
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (!qr.modules[y][x] || inFinder(x, y, size) || inAlign(x, y)) continue;
        parts.push(`<circle cx="${x + quiet + 0.5}" cy="${y + quiet + 0.5}" r="0.47"/>`);
      }
    }
    qr.align.forEach(([ax, ay]) => {
      const x = ax - 2 + quiet; const y = ay - 2 + quiet;
      parts.push(`<path fill-rule="evenodd" d="M${x + 1.2} ${y}h2.6a1.2 1.2 0 0 1 1.2 1.2v2.6a1.2 1.2 0 0 1-1.2 1.2h-2.6a1.2 1.2 0 0 1-1.2-1.2v-2.6a1.2 1.2 0 0 1 1.2-1.2z`
        + `M${x + 1.4} ${y + 1}h2.2a0.4 0.4 0 0 1 0.4 0.4v2.2a0.4 0.4 0 0 1-0.4 0.4h-2.2a0.4 0.4 0 0 1-0.4-0.4v-2.2a0.4 0.4 0 0 1 0.4-0.4z"/>`);
      parts.push(`<circle cx="${ax + quiet + 0.5}" cy="${ay + quiet + 0.5}" r="0.55"/>`);
    });
    [[0, 0], [size - 7, 0], [0, size - 7]].forEach(([fx, fy]) => {
      const x = fx + quiet; const y = fy + quiet;
      parts.push(`<path fill-rule="evenodd" d="M${x + 2} ${y}h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-3a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2z`
        + `M${x + 2.2} ${y + 1}h2.6a1.2 1.2 0 0 1 1.2 1.2v2.6a1.2 1.2 0 0 1-1.2 1.2h-2.6a1.2 1.2 0 0 1-1.2-1.2v-2.6a1.2 1.2 0 0 1 1.2-1.2z"/>`);
      parts.push(`<rect x="${x + 2}" y="${y + 2}" width="3" height="3" rx="0.9"/>`);
    });
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${full} ${full}" role="img"`
      + (opts.label ? ` aria-label="${String(opts.label).replace(/[&"<]/g, "")}"` : "")
      + `><g fill="${color}">${parts.join("")}</g></svg>`;
  }

  function wifiText(ssid, password) {
    const esc = (s) => String(s).replace(/([\\;,:"])/g, "\\$1");
    return password ? `WIFI:T:WPA;S:${esc(ssid)};P:${esc(password)};;` : `WIFI:T:nopass;S:${esc(ssid)};;`;
  }

  window.RukeboxQR = { encode, svg, wifiText };
})();
