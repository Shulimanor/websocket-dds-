// 前端逻辑冒烟测试：用最小 DOM stub 加载 index.html 的脚本，验证折叠/过滤/间隔。
// 运行: node test_frontend.mjs
import { readFileSync } from 'fs';

const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('FAIL: 未找到 <script>'); process.exit(1); }
const code = m[1];

// ---------- 最小 DOM stub ----------
function makeEl(tag) {
  const el = {
    tagName: tag, className: '', style: {}, children: [], textContent: '',
    _handlers: {}, _html: '', _value: '', _query: {}, firstChild: null,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      toggle(c, f) { if (f === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); return this._s.has(c); } f ? this._s.add(c) : this._s.delete(c); return f; },
      contains(c) { return this._s.has(c); },
    },
    set innerHTML(v) { this._html = String(v); this._parseQuery(String(v)); },
    get innerHTML() { return this._html !== undefined && this._html !== '' ? this._html : (this.textContent || ''); },
    _parseQuery(htmlStr) {
      const re2 = /<(span|pre|div) class="([^"]+)"[^>]*>/g;
      let mm;
      while ((mm = re2.exec(htmlStr))) {
        const cls = mm[2].split(/\s+/);
        if (cls.includes('log-toggle')) this._query['log-toggle'] = makeEl('span');
        if (cls.includes('log-body')) this._query['log-body'] = makeEl('span');
        if (cls.includes('log-int')) this._query['log-int'] = makeEl('span');
        if (cls.includes('json-pre')) this._query['json-pre'] = makeEl('pre');
        if (cls.includes('summary')) this._query['summary'] = makeEl('span');
      }
    },
    querySelector(sel) { return sel[0] === '.' ? (this._query[sel.slice(1)] || null) : null; },
    appendChild(c) { this.children.push(c); this.firstChild = this.children[0]; },
    remove() { /* no-op（测试内不超过 500 条） */ },
    addEventListener(t, fn) { this._handlers[t] = fn; },
    dispatch(t) { if (this._handlers[t]) this._handlers[t](); },
    set value(v) { this._value = v; },
    get value() { return this._value; },
  };
  return el;
}

const els = new Map();
global.document = {
  getElementById(id) { if (!els.has(id)) els.set(id, makeEl('div')); return els.get(id); },
  createElement(tag) { return makeEl(tag); },
  querySelector() { return makeEl('div'); },
  querySelectorAll() { return []; },
};
global.location = { protocol: 'http:', host: 'localhost:9999' };
class FakeWS { constructor() { this.readyState = FakeWS.CONNECTING; } }
FakeWS.OPEN = 1; FakeWS.CONNECTING = 0;
global.WebSocket = FakeWS;

// 运行面板脚本（间接 eval → function/const 进入全局作用域）
(0, eval)(code);

let pass = 0, fail = 0;
function check(name, cond) { if (cond) { pass++; } else { fail++; console.error('  ✗ ' + name); } }

// 注入：3 条 JSON + 1 条非 JSON
addLog('recv', 'dev', '10:00:01', JSON.stringify({ action: 'start_sim', utc_time: 1782357099827, sim_time: 0, info: { name: 'wanhua_test' }, task_id: 0 }));
addLog('recv', 'dev', '10:00:02', JSON.stringify({ action: 'update_boundle', utc_time: 1782357099900, sim_time: 1.5, task_id: 0 }));
addLog('recv', 'dev', '10:00:03', JSON.stringify({ action: 'start_sim', utc_time: 1782357100500, sim_time: 5, task_id: 1 }));
addLog('send', 'me', '10:00:04', 'plain text');

const log = document.getElementById('log');
const fltText = document.getElementById('fltText'), fltAction = document.getElementById('fltAction'),
      fltOnlyJson = document.getElementById('fltOnlyJson'), fltStats = document.getElementById('fltStats');

check('4 条消息已渲染', log.children.length === 4);
const e0 = log.children[0], e1 = log.children[1], e2 = log.children[2], e3 = log.children[3];
check('JSON 条目带 _obj', !!e0._obj && !!e2._obj && !e3._obj);
check('JSON 条目含 toggle/body/int', !!(e0.querySelector('.log-toggle') && e0.querySelector('.log-body') && e0.querySelector('.log-int')));

// 折叠/展开
check('默认折叠(无 expanded)', !e0.classList.contains('expanded'));
e0.querySelector('.log-toggle').dispatch('click');
check('点击 toggle 后展开', e0.classList.contains('expanded'));
e0.querySelector('.log-body').dispatch('click');
check('再点 body 后折叠', !e0.classList.contains('expanded'));

// action 下拉自动收集
check('action 下拉含 start_sim/update_boundle',
  fltAction.innerHTML.includes('start_sim') && fltAction.innerHTML.includes('update_boundle'));

// 手工设定到达时间，验证间隔徽标
e0._arr = 1000; e1._arr = 2000; e2._arr = 2500; e3._arr = 3000;

// 无过滤：4 条全可见，间隔为相邻差
applyFilter();
check('无过滤 4 条全显示', log.children.every(c => c.style.display !== 'none'));
check('无过滤统计', fltStats.textContent.includes('匹配 4 / 4'));
check('第1条无间隔', e0.querySelector('.log-int').textContent === '');
check('相邻间隔 1.0s', e1.querySelector('.log-int').textContent === '+1.0s');
check('相邻间隔 500ms', e2.querySelector('.log-int').textContent === '+500ms');

// 按 action 过滤
fltAction.value = 'start_sim';
applyFilter();
check('action=start_sim 可见 2 条', e0.style.display !== 'none' && e2.style.display !== 'none' && e1.style.display === 'none');
check('过滤统计', fltStats.textContent.includes('匹配 2 / 4'));
check('过滤后间隔(1500ms=1.5s)', e2.querySelector('.log-int').textContent === '+1.5s');

// key=value 过滤（info.name）
fltAction.value = ''; fltText.value = 'info.name=wanhua_test';
applyFilter();
check('info.name=wanhua_test 仅 e0', e0.style.display !== 'none' && e1.style.display === 'none' && e2.style.display === 'none');

// 关键字子串过滤
fltText.value = 'wanhua';
applyFilter();
check('子串 wanhua 仅 e0', e0.style.display !== 'none' && e1.style.display === 'none');

// 仅JSON
fltText.value = ''; fltOnlyJson.checked = true;
applyFilter();
check('仅JSON：非 JSON 隐藏', e3.style.display === 'none' && e0.style.display !== 'none');
check('仅JSON 统计', fltStats.textContent.includes('匹配 3 / 4'));

// 语法高亮含 token（从条目原始 HTML 里抽出 <pre> 内容）
const preM = e0._html.match(/<pre class="json-pre">([\s\S]*?)<\/pre>/);
const preHtml = preM ? preM[1] : '';
check('高亮含 jk/js/jn 类', /class="jk"/.test(preHtml) && /class="js"/.test(preHtml) && /class="jn"/.test(preHtml));
check('高亮 key 内容正确', preHtml.includes('"action"'));

// 展开全部按钮
const fltExpand = els.get('fltExpand');
fltExpand.dispatch('click');
check('展开全部后所有 JSON 展开', e0.classList.contains('expanded') && e2.classList.contains('expanded') && !e3.classList.contains('expanded'));
fltExpand.dispatch('click');
check('再次点击全部折叠', !e0.classList.contains('expanded') && !e2.classList.contains('expanded'));

console.log(`\n结果: ${pass} 通过, ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
