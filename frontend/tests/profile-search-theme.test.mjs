import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {transpile} from 'typescript';
import test from 'node:test';
import assert from 'node:assert/strict';

const source=readFileSync(new URL('../src/components/ProfileThemeRuntime.astro',import.meta.url),'utf8');
const setup=source.slice(source.indexOf('<script>')+8,source.indexOf('  if (!(window as Window'));
const apply=runInNewContext(transpile(`${setup};applyTheme;`),{window:{}});

test('list accent follows the owner while search follows only the viewer site-wide preference',()=>{
  for (const siteWide of [false,true]) {
    const values=new Map();
    const style={setProperty:(key,value)=>values.set(key,value),removeProperty:key=>values.delete(key)};
    const body={dataset:{cacheUser:'1',themeOwnerColor:'#ff3344',themeViewerColor:'#33aa66',themeSiteWide:String(siteWide)},style,ownerDocument:{documentElement:{style}}};
    apply(body);
    assert.equal(values.get('--blue'),'#ff3344');
    assert.equal(values.get('--quick-search-blue'),siteWide?'#33aa66':'#3db4f2');
  }
});
