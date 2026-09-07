"""Cross-language exact canonical commitments, including null, Unicode and int64."""
import json
from pathlib import Path
import subprocess
from bookflow.company.payment_queries import canonical,digest


def test_browser_and_python_commit_to_identical_bytes():
    static=Path(__file__).resolve().parents[1]/'src/bookflow/adapters/workbench/static'
    script=(static/'payments.js').read_text()
    start=script.index('  function canonical(value) {');end=script.index('  function exactUnits(',start)
    vectors=[dict(domain='bookflow.payment.recovery.intent',format=1,header_intent=dict(action='set',amount_origin='entered',amount_minor_units=9007199254740993,currency='USD'),entries=[dict(invoice_id='01AAAAAAAAAAAAAAAAAAAAAAAA',action='set',amount_origin='unresolved',amount_minor_units=None,currency='USD')]),
        dict(header_intent=dict(action='keep'),entries=[dict(action='calculate',attempted_calculated_minor_units=9223372036854775807)]),
        {'\U00010000':'supplementary','\ue000':'BMP','names':['Straße','東京','é','e\u0301','\u2028'], 'null':None},
        dict(header_intent=dict(action='set',amount_origin='selection_total'),entries=[]),dict(entries=[dict(action='calculate',attempted_calculated_minor_units=None)])]
    program="const {webcrypto}=require('node:crypto');const crypto=webcrypto;const window={};\n"+(static/'exact-json.js').read_text()+"\nconst exact=window.BookflowExactJSON;\n"+script[start:end]+"\n(async()=>{const fs=require('node:fs');const values=exact.parse(fs.readFileSync(0,'utf8'));const out=[];for(const value of values)out.push({canonical:canonical(value),digest:await digest(value)});process.stdout.write(JSON.stringify(out));})();"
    out=json.loads(subprocess.run(['node','-e',program],input=json.dumps(vectors,ensure_ascii=False),text=True,capture_output=True,check=True).stdout)
    assert out==[dict(canonical=canonical(value),digest=digest(value)) for value in vectors]
    assert digest({'value':None})!=digest({})
