import {header,authScreen} from './auth.js';
import {searchScreen} from './search.js';
import {lookupScreen} from './lookup.js';
const main=document.querySelector('#main');
let dispose=()=>{};
function render(){dispose();main.replaceChildren();header(render);if(location.pathname==='/login'||location.pathname==='/signup')authScreen(main,location.pathname==='/signup');else if(location.pathname==='/lookup')dispose=lookupScreen(main);else dispose=searchScreen(main);}
render();
