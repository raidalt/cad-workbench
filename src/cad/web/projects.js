const byId = (id) => document.getElementById(id);
const grid = byId('project-grid');
const message = byId('gallery-message');
const dialog = byId('project-dialog');
const form = byId('project-form');
const title = byId('project-title');
const save = byId('save-project');
const cancel = byId('cancel-project');
const formError = byId('project-error');
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });
let editedKey = null;
let library = [];
const filter = byId('project-filter');
const order = byId('project-order');

async function request(path = '', payload) {
  const response = await fetch('/api/projects' + path, payload === undefined
    ? { cache: 'no-store' }
    : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || 'The request failed.');
  return data;
}

async function openProject(key) {
  const { url } = await request('/open', { key });
  window.location.assign(url);
}

function showDialog(project) {
  editedKey = project?.key ?? null;
  byId('dialog-heading').textContent = project ? 'Rename project' : 'New project';
  title.value = project?.name ?? '';
  save.textContent = project ? 'Save name' : 'Create project';
  formError.textContent = '';
  dialog.showModal();
  title.select();
}

function card(project) {
  const item = document.createElement('li');
  item.className = 'project-card cad-surface';
  item.dataset.key = project.key;
  const open = document.createElement('button');
  open.className = 'project-open';
  open.type = 'button';
  open.setAttribute('aria-label', 'Open ' + project.name);
  open.addEventListener('click', async () => {
    open.disabled = true;
    message.textContent = 'Opening ' + project.name + '…';
    try {
      await openProject(project.key);
    } catch (error) {
      message.textContent = error.message;
      open.disabled = false;
    }
  });

  const preview = document.createElement('div');
  preview.className = 'project-preview';
  if (project.preview) {
    const image = document.createElement('img');
    image.src = project.preview;
    image.alt = '';
    image.loading = 'lazy';
    image.addEventListener('error', () => { preview.textContent = 'Preview unavailable'; });
    preview.append(image);
  } else {
    preview.textContent = 'No model yet';
  }
  const caption = document.createElement('div');
  caption.className = 'project-caption';
  const name = document.createElement('strong');
  name.textContent = project.name;
  caption.append(name);
  open.append(preview, caption);

  const meta = document.createElement('div');
  meta.className = 'project-meta';
  const date = document.createElement('time');
  const modified = new Date(project.modified * 1000);
  date.dateTime = modified.toISOString();
  date.textContent = dateFormat.format(modified);
  const rename = document.createElement('button');
  rename.type = 'button';
  rename.className = 'project-rename cad-button-ghost';
  rename.setAttribute('aria-label', 'Rename ' + project.name);
  rename.textContent = 'Rename';
  rename.addEventListener('click', () => showDialog(project));
  meta.append(date, rename);
  item.append(open, meta);
  return item;
}

function renderLibrary() {
  const query = filter.value.trim().toLocaleLowerCase();
  const projects = library.filter(project => project.name.toLocaleLowerCase().includes(query));
  projects.sort((a, b) => order.value === 'name'
    ? a.name.localeCompare(b.name, undefined, { numeric: true })
    : b.modified - a.modified);
  grid.replaceChildren(...projects.map(card));
  filter.parentElement.hidden = !library.length;
  message.textContent = !library.length ? 'Create your first project to start modeling.'
    : !projects.length ? 'No matching projects. Try another name.' : '';
}

filter.addEventListener('input', renderLibrary);
order.addEventListener('change', renderLibrary);

async function refresh() {
  try {
    const { projects } = await request();
    library = projects;
    renderLibrary();
  } catch (error) {
    message.textContent = error.message;
  }
}

byId('new-project').addEventListener('click', () => showDialog(null));
cancel.addEventListener('click', () => dialog.close());
dialog.addEventListener('cancel', (event) => {
  if (save.disabled) event.preventDefault();
});
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  cancel.disabled = save.disabled = true;
  formError.textContent = '';
  try {
    if (editedKey !== null) {
      await request('/rename', { key: editedKey, name: title.value });
      dialog.close();
      if (!title.value.toLocaleLowerCase().includes(filter.value.trim().toLocaleLowerCase())) filter.value = '';
      await refresh();
      grid.querySelector('[data-key="' + CSS.escape(editedKey) + '"] .project-rename')?.focus();
    } else {
      const { key } = await request('/create', { name: title.value });
      dialog.close();
      await refresh();
      message.textContent = 'Opening your project…';
      await openProject(key);
    }
  } catch (error) {
    (dialog.open ? formError : message).textContent = error.message;
  } finally {
    cancel.disabled = save.disabled = false;
  }
});
window.addEventListener('pageshow', () => void refresh());
