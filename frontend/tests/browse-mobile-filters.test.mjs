import assert from 'node:assert/strict';
import test from 'node:test';
import {initializeMobileBrowseFilters} from '../src/lib/browse-mobile-filters.ts';

test('mobile uses the same filters and restores desktop order across resize and navigation', t => {
  const media = new EventTarget();
  media.matches = true;
  const originals = new Map(['matchMedia', 'document'].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  t.after(() => {
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });
  Object.defineProperty(globalThis, 'matchMedia', {configurable: true, value: () => media});
  class Node {
    constructor(name) { this.name = name; this.children = []; }
    insert(index, child) {
      child.remove();
      child.parent = this;
      this.children.splice(index, 0, child);
    }
    remove() {
      if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1);
      this.parent = null;
    }
    before(child) { this.parent.insert(this.parent.children.indexOf(this), child); }
    after(child) { this.parent.insert(this.parent.children.indexOf(this) + 1, child); }
    prepend(...children) { children.forEach((child, index) => this.insert(index, child)); }
  }
  // Only node placement is mocked; identities and field state must survive moves.
  Object.defineProperty(globalThis, 'document', {configurable: true, value: {
    createComment: () => new Node('anchor'),
  }});
  const controls = new Node('controls');
  const fields = ['genres', 'status', 'sort'].map(name => new Node(name));
  const panel = new Node('panel');
  const secondary = new Node('provider');
  panel.prepend(secondary);
  controls.prepend(...fields);
  fields[0].value = '28';
  const attributes = new Set();
  const form = {
    querySelector: () => panel,
    querySelectorAll: () => fields,
    toggleAttribute: (name, on) => on ? attributes.add(name) : attributes.delete(name),
    removeAttribute: name => attributes.delete(name),
  };
  const controller = new AbortController();
  initializeMobileBrowseFilters(form, controller.signal);
  assert.deepEqual(panel.children, [...fields, secondary]);
  assert(attributes.has('data-mobile-filters'));
  media.matches = false;
  media.dispatchEvent(new Event('change'));
  assert.deepEqual(controls.children.filter(node => node.name !== 'anchor'), fields);
  assert.deepEqual(panel.children, [secondary]);
  assert.equal(fields[0].value, '28');
  media.matches = true;
  media.dispatchEvent(new Event('change'));
  assert.deepEqual(panel.children, [...fields, secondary]);
  controller.abort();
  assert.deepEqual(controls.children, fields);
  assert.deepEqual(panel.children, [secondary]);
  assert.equal(attributes.size, 0);
});
