// Apply a saved light/dark choice before first paint, so there's no flash.
try{const t=JSON.parse(localStorage.getItem("rsxp.theme"));if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch{}
