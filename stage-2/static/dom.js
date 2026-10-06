export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
    else if (value !== false && value != null) node.setAttribute(name, value === true ? '' : value);
  }
  node.append(...children.flat().filter(v => v != null));
  return node;
}
export const test = id => ({'data-testid': id});
export function field(label, id, attrs = {}) {
  const input = el('input', {id, ...test(id), ...attrs});
  return [el('div', {class:'field'}, el('label', {for:id}, label), input), input];
}
export function feedback(id, text, kind = 'error') {
  return el('div', {...test(id), class:`feedback ${kind}`, role:kind === 'error' ? 'alert' : 'status'}, text);
}
export function labels(detail, ids) {
  return ids.map(id => detail.tables.find(table => table.id === id)?.label ?? id).join(' & ');
}
export const members = record => record.table_ids ?? [record.table_id];
export const local = value => value.replace('T', ' · ');
