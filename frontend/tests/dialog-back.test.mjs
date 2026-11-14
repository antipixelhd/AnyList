import assert from 'node:assert/strict';
import test from 'node:test';
import {bindDialogBack} from '../src/lib/dialog-back.ts';

function fixture({activeDialog, dialogs = []} = {}) {
  const navigation = Object.assign(new EventTarget(), {currentEntry:{index:2}});
  bindDialogBack({
    defaultView:{navigation},
    activeElement:{closest:() => activeDialog},
    querySelectorAll:() => dialogs,
  });
  return ({index = 1, sameDocument = true, cancelable = true, navigationType = 'traverse'} = {}) => {
    const event = Object.assign(new Event('navigate', {cancelable}), {
      navigationType,
      destination:{index,sameDocument},
    });
    navigation.dispatchEvent(event);
    return event.defaultPrevented;
  };
}

test('Back requests close on the focused modal without changing page history', () => {
  const closed = [];
  const travel = fixture({
    activeDialog:{requestClose:() => closed.push('active')},
    dialogs:[{requestClose:() => closed.push('other')}],
  });
  assert.equal(travel(), true);
  assert.deepEqual(closed, ['active']);
});

test('Back falls through when no modal is open or when the browser disallows cancellation', () => {
  const travel = fixture({activeDialog:{requestClose:() => assert.fail('Unexpected close request')}});
  assert.equal(travel({cancelable:false}), false);
  assert.equal(travel({sameDocument:false}), false);
  assert.equal(travel({index:3}), false);
  assert.equal(travel({index:-1}), false);
  assert.equal(travel({navigationType:'push'}), false);
  assert.equal(fixture()(), false);
});

test('the dialog can veto dismissal when requestClose is unavailable', () => {
  const dialog = Object.assign(new EventTarget(), {close:() => assert.fail('Dialog vetoed close')});
  dialog.addEventListener('cancel', event => event.preventDefault());
  assert.equal(fixture({dialogs:[dialog]})(), true);
});
