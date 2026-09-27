/* Preserve accepted integer money across JSON transport; never round to Number. */
(() => {
  const limit=BigInt(Number.MAX_SAFE_INTEGER);
  function parse(text) {
    let prefix;
    do {prefix='bookflow-exact-'+Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('')+':';} while(text.includes(prefix));
    const protectedText=text.replace(/"(?:\\.|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g, token=>{
      if(!/^-?\d+$/.test(token)) return token;
      const value=BigInt(token);
      return value>limit||value< -limit?JSON.stringify(prefix+token):token;
    });
    return JSON.parse(protectedText,(_key,value)=>typeof value==='string'&&value.startsWith(prefix)?BigInt(value.slice(prefix.length)):value);
  }
  function stringify(value) {
    if(typeof value==='bigint') return value.toString();
    if(value===null||typeof value!=='object') return JSON.stringify(value);
    if(Array.isArray(value)) return '['+value.map(item=>stringify(item)??'null').join(',')+']';
    return '{'+Object.keys(value).filter(key=>value[key]!==undefined).map(key=>JSON.stringify(key)+':'+stringify(value[key])).join(',')+'}';
  }
  function minor(value,currency) {
    if(typeof value==='number'&&!Number.isSafeInteger(value)) throw Error('An inexact monetary value cannot be displayed or submitted. Reload the exact server facts.');
    const integer=BigInt(value), scale=JSON.parse(document.querySelector('#math-currencies').textContent)[currency]??2;
    const digits=(integer<0n?-integer:integer).toString().padStart(scale+1,'0');
    return (integer<0n?'-':'')+(scale?digits.slice(0,-scale)+'.'+digits.slice(-scale):digits);
  }
  /* How the page shows money and dates to a person, the way the server's pages do (display.py):
     "$1,855.95", a currency other than the company's home one keeps its code, "Nov 12" this
     year and "Nov 12, 2025" in another. Text only: the exact decimal string is regrouped, never
     turned into a number, and nothing shown this way is read back or sent. */
  const SYMBOLS={USD:'$',CAD:'$',AUD:'$',NZD:'$',EUR:'€',GBP:'£',JPY:'¥'};
  const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function grouped(value) {
    const match=/^([+-]?)(\d+)(\.\d+)?$/.exec(String(value??'').trim());
    return match?{sign:match[1]==='-'?'-':'',digits:match[2].replace(/\B(?=(\d{3})+(?!\d))/g,',')+(match[3]||'')}:null;
  }
  function amount(value) {
    const parts=grouped(value);
    return parts?parts.sign+parts.digits:String(value??'');
  }
  function money(value,currency) {
    const parts=grouped(value);
    if(!parts) return String(value??'');
    const symbol=SYMBOLS[currency], home=document.body?.dataset.mathCompanyCurrency||'';
    if(symbol===undefined) return currency?`${parts.sign}${parts.digits} ${currency}`:parts.sign+parts.digits;
    return parts.sign+symbol+parts.digits+(home&&currency!==home?' '+currency:'');
  }
  function day(value,long=false) {
    const match=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(value??''));
    if(!match) return value==null?'':String(value);
    const shown=MONTHS[Number(match[2])-1]+' '+Number(match[3]);
    const year=(document.body?.dataset.companyToday||new Date().toISOString()).slice(0,4);
    return !long&&match[1]===year?shown:shown+', '+match[1];
  }
  window.BookflowExactJSON={parse,stringify,minor,amount,money,day,longday:value=>day(value,true)};
})();
