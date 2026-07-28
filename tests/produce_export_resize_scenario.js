'use strict';

const { realKeyPress } = require('./produce_export_browser_helpers.js');

const json = (value) => JSON.stringify(value);

async function exerciseColumnResize(session) {
  const selector = '[data-produce-column-resize="character"]';
  const read = () => session.evaluate(`(() => {
    const content=document.querySelector('.produce-content');
    const handle=document.querySelector(${json(selector)});
    let saved={};
    try { saved=JSON.parse(localStorage.getItem('alexandria.produce.columns.v3')||'{}'); } catch (_) {}
    return {
      handles:document.querySelectorAll('[data-produce-column-resize]').length,
      visibleHandles:[...document.querySelectorAll('[data-produce-column-resize]')]
        .filter((node)=>node.offsetParent!==null&&node.getBoundingClientRect().width>0).length,
      width:parseFloat(getComputedStyle(content).getPropertyValue('--produce-character-column')),
      defaultWidth:parseFloat(getComputedStyle(content).getPropertyValue('--produce-character-default')),
      ariaValue:Number(handle?.getAttribute('aria-valuenow')),
      savedWidth:Number(saved.character), focused:document.activeElement===handle,
    };
  })()`);
  const before = await read();
  const target = await session.evaluate(`(async () => {
    const node=document.querySelector(${json(selector)});if(!node) return null;
    node.scrollIntoView({block:'center',inline:'nearest'});
    await new Promise((resolve)=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const rect=node.getBoundingClientRect();
    if(node.offsetParent===null||rect.width<=0||rect.height<=0) return {visible:false};
    const x=rect.left+rect.width/2,y=rect.top+rect.height/2,hit=document.elementFromPoint(x,y);
    return {x,y,left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,
      viewport:{width:innerWidth,height:innerHeight},hit:Boolean(hit&&(hit===node||node.contains(hit))),
      hitNode:hit?(hit.tagName+'.'+hit.className):null};
  })()`);
  if (!target?.hit) return { mechanism: 'CDP pointer/key input', available: false, before, target };
  await session.client.send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: target.x, y: target.y, button: 'left', buttons: 1, clickCount: 1,
  });
  await session.client.send('Input.dispatchMouseEvent', {
    type: 'mouseMoved', x: target.x + 24, y: target.y, button: 'left', buttons: 1,
  });
  await session.client.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: target.x + 24, y: target.y, button: 'left', buttons: 0, clickCount: 1,
  });
  await session.evaluate(`new Promise((resolve)=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
  const afterPointer = await read();
  await session.evaluate(`document.querySelector(${json(selector)})?.focus()`);
  const focused = (await read()).focused;
  await realKeyPress(session, 'ArrowLeft', 'ArrowLeft');
  const afterLeft = await read();
  await realKeyPress(session, 'ArrowRight', 'ArrowRight');
  const afterRight = await read();
  await realKeyPress(session, 'Home', 'Home');
  const afterHome = await read();
  await realKeyPress(session, 'ArrowRight', 'ArrowRight');
  const persisted = await read();
  return {
    mechanism: 'CDP Input.dispatchMouseEvent/Input.dispatchKeyEvent',
    available: true, target, before, afterPointer, focused, afterLeft, afterRight, afterHome, persisted,
    pointerDrag: afterPointer.width > before.width && afterPointer.savedWidth === afterPointer.width,
    arrowLeft: afterLeft.width < afterPointer.width && afterLeft.savedWidth === afterLeft.width,
    arrowRight: afterRight.width > afterLeft.width && afterRight.savedWidth === afterRight.width,
    homeReset: afterHome.width === before.defaultWidth && afterHome.savedWidth === afterHome.width,
    savedCustomWidth: persisted.width > afterHome.width && persisted.savedWidth === persisted.width,
  };
}

module.exports = { exerciseColumnResize };
