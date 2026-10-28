// Whether this page may use the camera (R190). Browsers allow getUserMedia only on a secure page: https,
// or http://localhost / 127.0.0.1 on the same computer. The host says whether a public https origin is
// configured (GET /secure-context; design/https-serving.md). bookflowSecureContext() resolves to
//   {secure, https, public_origin, camera: 'live' | 'photo', message}
// 'live' means a camera stream can open here; 'photo' means offer photo capture (<input type=file
// capture>), which works on any page, and show `message` to explain why the live camera is off.
(() => {
  if (window.bookflowSecureContext) return;
  let asked = null;
  window.bookflowSecureContext = () => {
    if (!asked) {
      asked = fetch('/secure-context', {credentials: 'same-origin'})
        .then(r => (r.ok ? r.json() : {}))
        .catch(() => ({}))
        .then(host => {
          const secure = !!window.isSecureContext;
          const https = !!host.https;
          const live = secure && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
          let message = '';
          if (!secure) {
            message = https && host.public_origin
              ? `The camera needs a secure page. Open Bookflow at ${host.public_origin} instead.`
              : 'The camera needs a secure (https) page. Ask your administrator to serve Bookflow over https (for example with `colony serve`); until then, take a photo instead.';
          } else if (!live) {
            message = 'This browser offers no live camera here; take a photo instead.';
          }
          return {secure, https, public_origin: host.public_origin || null, camera: live ? 'live' : 'photo', message};
        });
    }
    return asked;
  };
})();
