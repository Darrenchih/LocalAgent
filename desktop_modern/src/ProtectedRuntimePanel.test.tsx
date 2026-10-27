import { render, screen, waitFor, cleanup, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProtectedRuntimePanel from './ProtectedRuntimePanel';
import i18n from './i18n';
const {invoke}=vi.hoisted(()=>({invoke:vi.fn()}));
vi.mock('@tauri-apps/api/core',()=>({invoke}));
const status=(state='disabled')=>({state,execution_enabled:false,error:null,bundle_id:'fixture',artifact_version:'1',last_health_ms:null});
beforeEach(async()=>{await i18n.changeLanguage('en');invoke.mockReset();invoke.mockResolvedValue(status());});
afterEach(()=>{cleanup();vi.useRealTimers();});
it('starts and stops management with argument-free commands',async()=>{
 render(<ProtectedRuntimePanel/>);await waitFor(()=>expect(screen.getByRole('button',{name:'Start'})).toBeEnabled());
 expect(screen.getByText(/Management Only/)).toBeInTheDocument();expect(screen.getByText('Execution Disabled')).toBeInTheDocument();
 invoke.mockResolvedValueOnce(status('starting'));await userEvent.click(screen.getByRole('button',{name:'Start'}));
 expect(invoke).toHaveBeenLastCalledWith('protected_runtime_start');expect(screen.getByRole('button',{name:'Start'})).toBeDisabled();
 invoke.mockResolvedValueOnce(status('stopped'));await userEvent.click(screen.getByRole('button',{name:'Stop'}));expect(invoke).toHaveBeenLastCalledWith('protected_runtime_stop');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Start'})).toBeEnabled());
});
it.each([{...status('ready'),execution_enabled:true},status('invented')])('rejects unsafe or unknown status',async(value)=>{invoke.mockResolvedValue(value);render(<ProtectedRuntimePanel/>);expect(await screen.findByRole('alert')).toBeInTheDocument();expect(screen.queryByText('Ready (management healthy only)')).toBeNull();});
it('polls backend liveness and cleans up after unmount',async()=>{
 vi.useFakeTimers();const view=render(<ProtectedRuntimePanel/>);await act(async()=>{});
 invoke.mockResolvedValue(status('failed'));await act(async()=>{await vi.advanceTimersByTimeAsync(2000);});expect(screen.getByText('Failed')).toBeInTheDocument();
 view.unmount();const calls=invoke.mock.calls.length;await act(async()=>{await vi.advanceTimersByTimeAsync(4000);});expect(invoke).toHaveBeenCalledTimes(calls);
});
it('ignores an older refresh completing after start',async()=>{
 let resolve!:(v:unknown)=>void;invoke.mockResolvedValueOnce(status()).mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));render(<ProtectedRuntimePanel/>);
 await waitFor(()=>expect(screen.getByRole('button',{name:'Start'})).toBeEnabled());await userEvent.click(screen.getByRole('button',{name:'Refresh'}));
 invoke.mockResolvedValueOnce(status('starting'));await userEvent.click(screen.getByRole('button',{name:'Start'}));await act(async()=>resolve(status('ready')));
 expect(screen.getByText('Starting')).toBeInTheDocument();
});

