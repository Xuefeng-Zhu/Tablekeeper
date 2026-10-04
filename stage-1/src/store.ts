import { empty, type State } from "./model.js";
import { clone } from "./validation.js";
function freeze(x: any): any {
  if (x && typeof x === "object" && !Object.isFrozen(x)) {
    Object.freeze(x);
    for (const v of Object.values(x)) freeze(v);
  }
  return x;
}
let root: State = freeze(empty());
export function snapshot(): State {
  return root;
}
export function replace(s: State) {
  root = freeze(clone(s));
}
export function transact<T>(fn: (s: State) => T): T {
  const next = clone(root);
  const result = clone(fn(next));
  root = freeze(next);
  return result;
}
