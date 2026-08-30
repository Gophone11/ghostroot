from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_translation_prefers_backend_translate_endpoint() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required for static UI behavior tests")

    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    script = f"""
const fs = require('fs');
const html = fs.readFileSync({str(index_path)!r}, 'utf8');
const start = html.indexOf('const I18N =');
const end = html.indexOf('</script>', start);
if (start < 0 || end < 0) throw new Error('app script not found');
eval(html.slice(start, end) + '\\n;globalThis.ghostrootApp = ghostrootApp;');

globalThis.window = {{ addEventListener() {{}} }};
globalThis.location = {{ hash: '#/' }};
globalThis.localStorage = {{ getItem() {{ return null; }}, setItem() {{}} }};
globalThis.document = {{}};

const app = globalThis.ghostrootApp();
app.locale = 'zh-CN';

const calls = [];
globalThis.fetch = async (url, options = {{}}) => {{
  calls.push(String(url));
  if (String(url) !== '/translate') throw new Error(`unexpected external translator call: ${{url}}`);
  const body = JSON.parse(options.body || '{{}}');
  if (body.text !== 'This fact records a service observation.') throw new Error(`wrong body text: ${{options.body}}`);
  if (body.target_lang !== 'zh-CN') throw new Error(`wrong target lang: ${{options.body}}`);
  return {{
    ok: true,
    status: 200,
    json: async () => ({{ translated_text: '该记录描述了一项服务观察。', provider: 'test' }}),
  }};
}};

(async () => {{
  await app.doTranslate('This fact records a service observation.');
  if (app.translatedText !== '该记录描述了一项服务观察。') throw new Error(`backend translation was not used: ${{app.translatedText}}`);
  if (calls.length !== 1) throw new Error(`expected one backend call, got ${{calls.length}}`);
}})().catch(error => {{
  console.error(error);
  process.exit(1);
}});
"""

    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr


def test_translation_keeps_partial_result_when_a_later_chunk_fails() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required for static UI behavior tests")

    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    script = f"""
const fs = require('fs');
const html = fs.readFileSync({str(index_path)!r}, 'utf8');
const start = html.indexOf('const I18N =');
const end = html.indexOf('</script>', start);
if (start < 0 || end < 0) throw new Error('app script not found');
eval(html.slice(start, end) + '\\n;globalThis.ghostrootApp = ghostrootApp;');

globalThis.window = {{ addEventListener() {{}} }};
globalThis.location = {{ hash: '#/' }};
globalThis.localStorage = {{ getItem() {{ return null; }}, setItem() {{}} }};
globalThis.document = {{}};

const app = globalThis.ghostrootApp();
app.locale = 'zh-CN';
const toasts = [];
app.showToast = (message, type = 'info') => toasts.push({{ message, type }});

let calls = 0;
globalThis.fetch = async (url) => {{
  if (String(url) === '/translate') {{
    return {{ ok: false, status: 503, json: async () => ({{ detail: 'not configured' }}) }};
  }}
  calls += 1;
  if (calls === 1) {{
    return {{ ok: true, json: async () => ({{ responseData: {{ translatedText: '第一段' }} }}) }};
  }}
  return {{ ok: false, status: 500, json: async () => ({{}}) }};
}};

const text = `${{'A'.repeat(460)}} ${{'B'.repeat(80)}}`;

(async () => {{
  await app.doTranslate(text);
  const failed = toasts.some(toast => toast.type === 'error' && toast.message === '翻译失败');
  if (failed) throw new Error(`unexpected Translation failed toast: ${{JSON.stringify(toasts)}}`);
  if (!app.translatedText.startsWith('第一段')) throw new Error(`missing translated first chunk: ${{app.translatedText}}`);
  if (!app.translatedText.includes('BBBB')) throw new Error(`missing fallback source chunk: ${{app.translatedText}}`);
  if (calls !== 2) throw new Error(`expected 2 chunk fetches, got ${{calls}}`);
}})().catch(error => {{
  console.error(error);
  process.exit(1);
}});
"""

    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr


def test_translation_falls_back_when_all_chunks_fail() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required for static UI behavior tests")

    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    script = f"""
const fs = require('fs');
const html = fs.readFileSync({str(index_path)!r}, 'utf8');
const start = html.indexOf('const I18N =');
const end = html.indexOf('</script>', start);
if (start < 0 || end < 0) throw new Error('app script not found');
eval(html.slice(start, end) + '\\n;globalThis.ghostrootApp = ghostrootApp;');

globalThis.window = {{ addEventListener() {{}} }};
globalThis.location = {{ hash: '#/' }};
globalThis.localStorage = {{ getItem() {{ return null; }}, setItem() {{}} }};
globalThis.document = {{}};

const app = globalThis.ghostrootApp();
app.locale = 'zh-CN';
const toasts = [];
app.showToast = (message, type = 'info') => toasts.push({{ message, type }});

globalThis.fetch = async () => {{
  throw new Error('network blocked');
}};

(async () => {{
  await app.doTranslate('decision: probe the login form. kind=validate; status=In Progress; route=origin -> open; stop=credential confirmed');
  const failed = toasts.some(toast => toast.type === 'error' && toast.message === '翻译失败');
  if (failed) throw new Error(`unexpected Translation failed toast: ${{JSON.stringify(toasts)}}`);
  if (!app.translatedText.includes('决策：')) throw new Error(`missing local fallback translation: ${{app.translatedText}}`);
  if (!app.translatedText.includes('类型=')) throw new Error(`missing translated structured label: ${{app.translatedText}}`);
}})().catch(error => {{
  console.error(error);
  process.exit(1);
}});
"""

    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr


def test_natural_summary_fallback_translation_is_chinese() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required for static UI behavior tests")

    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    script = f"""
const fs = require('fs');
const html = fs.readFileSync({str(index_path)!r}, 'utf8');
const start = html.indexOf('const I18N =');
const end = html.indexOf('</script>', start);
if (start < 0 || end < 0) throw new Error('app script not found');
eval(html.slice(start, end) + '\\n;globalThis.ghostrootApp = ghostrootApp;');

globalThis.window = {{ addEventListener() {{}} }};
globalThis.location = {{ hash: '#/' }};
globalThis.localStorage = {{ getItem() {{ return null; }}, setItem() {{}} }};
globalThis.document = {{}};

const app = globalThis.ghostrootApp();
app.locale = 'zh-CN';
globalThis.fetch = async () => {{
  throw new Error('network blocked');
}};

(async () => {{
  const source = 'This fact records an exploration result observation with a positive outcome. Its relevance to the project goal is proves goal.';
  await app.doTranslate(source);
  if (app.translatedText.includes('This fact records')) throw new Error(`fallback stayed English: ${{app.translatedText}}`);
  if (!app.translatedText.includes('该事实记录')) throw new Error(`missing Chinese fact sentence: ${{app.translatedText}}`);
  if (!app.translatedText.includes('证明目标')) throw new Error(`missing translated value: ${{app.translatedText}}`);
}})().catch(error => {{
  console.error(error);
  process.exit(1);
}});
"""

    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr


def test_mixed_summary_translation_postprocesses_identity_result() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required for static UI behavior tests")

    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    script = f"""
const fs = require('fs');
const html = fs.readFileSync({str(index_path)!r}, 'utf8');
const start = html.indexOf('const I18N =');
const end = html.indexOf('</script>', start);
if (start < 0 || end < 0) throw new Error('app script not found');
eval(html.slice(start, end) + '\\n;globalThis.ghostrootApp = ghostrootApp;');

globalThis.window = {{ addEventListener() {{}} }};
globalThis.location = {{ hash: '#/' }};
globalThis.localStorage = {{ getItem() {{ return null; }}, setItem() {{}} }};
globalThis.document = {{}};

const source = '该事实记录了一条goal proof观察，结果为goal proof。 它与项目目标的关系是证明目标。 建议的下一步是complete。 标签为proof、flag、ctf。 The supporting evidence says that target has vulnerability SQL injection at /se3reTdir777/ via uid POST parameter (positive); attacker exploited SQL injection to extract credentials from systemUser table (t00r, aiweb1pwn, u3er) (positive); attacker used INTO OUTFILE to write webshell shell.php to uploads directory (positive); attacker executed commands as www data via webshell at /se3reTdir777/uploads/shell.php (positive); flag found at /tmp/flag.txt with value flag{{cbe5831d864cbc2a104e2c2b9dfb50e5acbdee71}} (positive).';

const app = globalThis.ghostrootApp();
app.locale = 'zh-CN';
globalThis.fetch = async (url) => {{
  if (String(url) === '/translate') {{
    return {{ ok: false, status: 503, json: async () => ({{ detail: 'not configured' }}) }};
  }}
  const query = String(url).split('?')[1] || '';
  const q = new URLSearchParams(query).get('q') || '';
  return {{ ok: true, json: async () => ({{ responseData: {{ translatedText: q }} }}) }};
}};

(async () => {{
  await app.doTranslate(source);
  if (app.translatedText.includes('The supporting evidence says that')) throw new Error(`evidence header stayed English: ${{app.translatedText}}`);
  if (!app.translatedText.includes('支持证据表明：')) throw new Error(`missing Chinese evidence header: ${{app.translatedText}}`);
  if (!app.translatedText.includes('/se3reTdir777/uploads/shell.php')) throw new Error(`path was corrupted: ${{app.translatedText}}`);
  if (!app.translatedText.includes('/tmp/flag.txt')) throw new Error(`flag path was corrupted: ${{app.translatedText}}`);
}})().catch(error => {{
  console.error(error);
  process.exit(1);
}});
"""

    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr


def test_proftpd_evidence_translation_postprocesses_full_evidence() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required for static UI behavior tests")

    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    script = f"""
const fs = require('fs');
const html = fs.readFileSync({str(index_path)!r}, 'utf8');
const start = html.indexOf('const I18N =');
const end = html.indexOf('</script>', start);
if (start < 0 || end < 0) throw new Error('app script not found');
eval(html.slice(start, end) + '\\n;globalThis.ghostrootApp = ghostrootApp;');

globalThis.window = {{ addEventListener() {{}} }};
globalThis.location = {{ hash: '#/' }};
globalThis.localStorage = {{ getItem() {{ return null; }}, setItem() {{}} }};
globalThis.document = {{}};

const source = String.raw`This fact records an exploration result observation with a positive outcome. Its relevance to the project goal is proves goal. The recommended next step is complete. It is tagged as cve 2010 4221, proftpd, backdoor, root, initial access. The supporting evidence says that ProFTPD 1.3.3c at 192.168.24.130:21 has backdoor CVE 2010 4221 triggered via HELP ACIDBITCHEZ (positive); backdoor shell requires LF only (\\n) command termination after trigger; CRLF (\\r\\n) fails (positive); backdoor shell provides direct root shell (uid=0) on same FTP connection (positive); Metasploit exploit/unix/ftp/proftpd 133c backdoor failed to create session with default payloads (likely CRLF issue) (negative); attacker achieved remote root code execution on 192.168.24.130 (positive); target 192.168.24.130 is vtcsec, Ubuntu 16.04, Linux 4.10.0 28 generic x86 64 (positive); user marlinspike (uid 1000) has file backdoored proftpd 1.3.3c.tar.gz in home directory (positive).`;

const app = globalThis.ghostrootApp();
app.locale = 'zh-CN';
globalThis.fetch = async (url) => {{
  if (String(url) === '/translate') {{
    return {{ ok: false, status: 503, json: async () => ({{ detail: 'not configured' }}) }};
  }}
  const query = String(url).split('?')[1] || '';
  const q = new URLSearchParams(query).get('q') || '';
  return {{ ok: true, json: async () => ({{ responseData: {{ translatedText: q }} }}) }};
}};

(async () => {{
  await app.doTranslate(source);
  const output = app.translatedText;
  for (const forbidden of [
    'The supporting evidence says that',
    'has backdoor',
    'backdoor shell requires',
    'provides direct root shell',
    'failed to create session',
    'attacker achieved remote root code execution',
    'has file backdoored',
  ]) {{
    if (output.includes(forbidden)) throw new Error(`untranslated evidence fragment "${{forbidden}}": ${{output}}`);
  }}
  for (const required of [
    '支持证据表明',
    '存在后门 CVE-2010-4221',
    '通过输入“HELP ACIDBITCHEZ”触发',
    '后门 shell 要求',
    '直接 root shell',
    '无法建立会话',
    '远程 root 代码执行',
    '运行 Ubuntu 16.04',
    'home 目录中存在被植入后门的文件',
  ]) {{
    if (!output.includes(required)) throw new Error(`missing translated evidence fragment "${{required}}": ${{output}}`);
  }}
  if (!output.includes('\\\\n') || !output.includes('\\\\r\\\\n')) throw new Error(`line endings were corrupted: ${{output}}`);
  if (!output.includes('proftpd 1.3.3c.tar.gz')) throw new Error(`artifact was corrupted: ${{output}}`);
}})().catch(error => {{
  console.error(error);
  process.exit(1);
}});
"""

    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr


def test_translate_buttons_remain_visible_after_translation() -> None:
    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    html = index_path.read_text()

    assert "x-show=\"factUserSummary(selectedFactRecord()) && !translationMatches" not in html
    assert "x-show=\"intentUserSummary(selectedIntentRecord()) && !translationMatches" not in html
    assert "x-show=\"factUserSummary(selectedFactRecord())\"" in html
    assert "x-show=\"intentUserSummary(selectedIntentRecord())\"" in html


def test_project_metrics_use_single_project_labels() -> None:
    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    html = index_path.read_text()

    assert ">AS<" not in html
    assert ">AE<" not in html
    assert ">Steps<" in html
    assert ">Runs<" not in html
    assert ">Episodes<" in html
    assert "projectSteps(selectedProjectId)" in html
    assert "projectEpisodes(selectedProjectId)" in html


def test_project_view_can_export_tool_events() -> None:
    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    html = index_path.read_text()

    assert "downloadProjectToolEvents(selectedProjectId)" in html
    assert "/export?format=tool-events" in html
    assert "-tool-events.json" in html
    assert "Tool Calls" in html
    assert "Export tool calls" in html


def test_intel_card_graph_labels_and_edges_are_structured() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required for static UI behavior tests")

    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    script = f"""
const fs = require('fs');
const html = fs.readFileSync({str(index_path)!r}, 'utf8');
const start = html.indexOf('const I18N =');
const end = html.indexOf('</script>', start);
if (start < 0 || end < 0) throw new Error('app script not found');
eval(html.slice(start, end) + '\\n;globalThis.ghostrootApp = ghostrootApp;');

globalThis.window = {{ addEventListener() {{}} }};
globalThis.location = {{ hash: '#/' }};
globalThis.localStorage = {{ getItem() {{ return null; }}, setItem() {{}} }};
globalThis.document = {{}};

const app = globalThis.ghostrootApp();
if (app.layoutMode !== 'dagre_lr') throw new Error(`default layout is not Dagre right: ${{app.layoutMode}}`);
if (!html.includes('id="intelCardLayer"')) throw new Error('intel card overlay layer is missing');
if (!html.includes('.intel-atom-card')) throw new Error('atom card CSS is missing');

const fact = {{
  id: 'f001',
  kind: 'goal_proof',
  outcome: 'goal_proof',
  goal_relevance: 'proves_goal',
  tags: ['root', 'vsftpd-backdoor'],
  atoms: [
    {{ subject: 'attacker', predicate: 'got_root_shell_on', object: 'target via vsFTPd', polarity: 'positive' }},
    {{ subject: 'exploit', predicate: 'is', object: 'CVE-2011-2523', polarity: 'positive' }},
    {{ subject: 'shell_command_id', predicate: 'returned', object: 'uid=0(root)', polarity: 'positive' }},
  ],
}};
const label = app.summarizeFactLabel(fact);
if (label !== 'attacker got root shell on target via vsFTPd') throw new Error(`structured fact summary should stay atom-headline style: ${{label}}`);
const model = app.intelCardModel(fact);
if (model.id !== 'f001' || model.kind !== 'goal proof') throw new Error(`overlay model missed header data: ${{JSON.stringify(model)}}`);
if (model.summary !== label) throw new Error(`overlay summary diverged from atom headline: ${{JSON.stringify(model)}}`);
if (!model.atoms.includes('attacker → got root shell on → target via vsFTPd')) throw new Error(`missing first atom card line: ${{JSON.stringify(model)}}`);
if (!model.atoms.includes('exploit → is → CVE-2011-2523')) throw new Error(`missing second atom card line: ${{JSON.stringify(model)}}`);
if (model.extraAtoms !== 1) throw new Error(`missing folded atom count: ${{JSON.stringify(model)}}`);
const htmlCard = app.intelCardHtml(model);
if (!htmlCard.includes('class="intel-atom-card"')) throw new Error(`overlay html did not render atom cards: ${{htmlCard}}`);
if (!htmlCard.includes('class="intel-card-more">+1')) throw new Error(`overlay html did not render folded atom badge: ${{htmlCard}}`);
if (label.includes('This fact records')) throw new Error(`label kept natural-language template: ${{label}}`);
if (app.factBorderColor(fact) !== '#22c55e') throw new Error(`goal proof border color wrong: ${{app.factBorderColor(fact)}}`);
if (app.summarizeFactLabel({{ id: 'origin', description: 'start' }}) !== 'Origin') throw new Error('origin style changed');
if (app.summarizeFactLabel({{ id: 'goal', description: 'finish' }}) !== 'Goal') throw new Error('goal style changed');

const exploitIntent = {{ kind: 'exploit', description: 'very long exploit description that must not appear on the edge' }};
if (app.intentActionLabel(exploitIntent) !== 'exploit') throw new Error('intent kind was not used');
const inferred = app.intentActionLabel({{ description: 'validate whether the shell is root' }});
if (inferred !== 'validate') throw new Error(`intent action inference failed: ${{inferred}}`);
const style = app.intentEdgeStyle({{ kind: 'branch', description: 'try another path' }});
if (style.edgeLineStyle !== 'dashed') throw new Error(`branch edge is not dashed: ${{JSON.stringify(style)}}`);
"""

    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr


def test_structured_detail_summaries_are_complete_sentences() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is required for static UI behavior tests")

    index_path = Path(__file__).parents[1] / "src/ghostroot/server/static/index.html"
    script = f"""
const fs = require('fs');
const html = fs.readFileSync({str(index_path)!r}, 'utf8');
const start = html.indexOf('const I18N =');
const end = html.indexOf('</script>', start);
if (start < 0 || end < 0) throw new Error('app script not found');
eval(html.slice(start, end) + '\\n;globalThis.ghostrootApp = ghostrootApp;');

globalThis.window = {{ addEventListener() {{}} }};
globalThis.location = {{ hash: '#/' }};
globalThis.localStorage = {{ getItem() {{ return null; }}, setItem() {{}} }};
globalThis.document = {{}};

const app = globalThis.ghostrootApp();
const fact = {{
  id: 'f1',
  kind: 'exploration_result',
  outcome: 'partial_progress',
  goal_relevance: 'advances',
  next_policy: 'branch',
  tags: ['auth_surface', 'sql_probe'],
  atoms: Array.from({{ length: 6 }}, (_, index) => ({{
    subject: `endpoint_${{index}}`,
    predicate: 'reveals',
    object: `signal_${{index}}`,
    polarity: 'positive',
  }})),
}};
const siblingFact = {{
  id: 'f2',
  kind: 'exploration_result',
  outcome: 'positive',
  goal_relevance: 'advances',
  next_policy: 'branch',
  tags: ['ftp', 'backdoor'],
  atoms: [{{
    subject: 'proftpd_1.3.3c',
    predicate: 'exposes',
    object: 'root_shell',
    polarity: 'positive',
  }}],
}};
const intent = {{
  id: 'i1',
  kind: 'validate',
  description: 'validate whether the login form accepts boolean SQL injection',
  from: ['origin', 'f1'],
  to: null,
  stop_condition: 'the injection hypothesis is confirmed or rejected',
}};

const factSummary = app.factUserSummary(fact);
const factCardSummary = app.factCardSummary(fact);
const siblingFactCardSummary = app.factCardSummary(siblingFact);
const intentSummary = app.intentUserSummary(intent);
if (factSummary.includes('...') || factSummary.includes('…')) throw new Error(`fact summary was truncated: ${{factSummary}}`);
if (!factSummary.includes('endpoint 5 reveals signal 5')) throw new Error(`fact summary omitted evidence: ${{factSummary}}`);
if (factSummary.includes('kind=') || factSummary.includes('outcome=') || factSummary.includes('evidence:')) throw new Error(`fact summary is field-pasted: ${{factSummary}}`);
if (!factSummary.includes('This fact records')) throw new Error(`fact summary is not sentence-like: ${{factSummary}}`);
if (factCardSummary.includes('This fact records')) throw new Error(`card summary kept template opener: ${{factCardSummary}}`);
if (!factCardSummary.includes('endpoint 0 reveals signal 0')) throw new Error(`card summary missed primary evidence: ${{factCardSummary}}`);
if (!siblingFactCardSummary.includes('proftpd 1.3.3c exposes root shell')) throw new Error(`sibling card summary missed distinct evidence: ${{siblingFactCardSummary}}`);
if (factCardSummary === siblingFactCardSummary) throw new Error(`card summaries were not differentiated: ${{factCardSummary}}`);
if (intentSummary.includes('kind=') || intentSummary.includes('status=') || intentSummary.includes('route=')) throw new Error(`intent summary is field-pasted: ${{intentSummary}}`);
if (!intentSummary.includes('The agent decided to')) throw new Error(`intent summary is not sentence-like: ${{intentSummary}}`);
"""

    result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
