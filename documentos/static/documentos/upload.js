(() => {
  const input = document.querySelector('.file-input');
  const zone = document.querySelector('#dropzone');
  const list = document.querySelector('#file-list');
  const choose = document.querySelector('#select-files');
  const form = document.querySelector('#upload-form');
  if (!input || !zone || !list || !choose || !form) return;
  let files = [];
  const formatSize = (size) => `${(size / 1024 / 1024).toFixed(size > 1024 * 1024 ? 1 : 2)} MB`;
  const sync = () => {
    const transfer = new DataTransfer(); files.forEach(file => transfer.items.add(file)); input.files = transfer.files;
    list.innerHTML = '';
    files.forEach((file, index) => { const item = document.createElement('li'); item.innerHTML = `<span>${file.name} <small>${formatSize(file.size)}</small></span><button type="button" aria-label="Retirar ${file.name}">Retirar</button>`; item.querySelector('button').onclick = () => { files.splice(index, 1); sync(); }; list.appendChild(item); });
  };
  const add = (incoming) => { files = files.concat([...incoming]); sync(); };
  choose.onclick = () => input.click();
  input.onchange = () => add(input.files);
  ['dragenter','dragover'].forEach(event => zone.addEventListener(event, e => { e.preventDefault(); zone.classList.add('dragging'); }));
  ['dragleave','drop'].forEach(event => zone.addEventListener(event, e => { e.preventDefault(); zone.classList.remove('dragging'); }));
  zone.addEventListener('drop', event => add(event.dataTransfer.files));
  form.addEventListener('submit', () => { const button = document.querySelector('#submit-button'); button.disabled = true; button.textContent = 'Procesando facturas...'; });
})();
