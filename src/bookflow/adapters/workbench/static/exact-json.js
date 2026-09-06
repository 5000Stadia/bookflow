/* Preserve accepted integer money across JSON transport; never round to Number. */
(() => {
  const limit=BigInt(Number.MAX_SAFE_INTEGER);
  function parse(text) {
    let prefix;
    do {prefix='bookflow-exact-'+crypto.randomUUID()+':';} while(text.includes(prefix));
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
  window.BookflowExactJSON={parse,stringify,minor};
})();
