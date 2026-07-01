import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from '../api/client';

export default function ResumeEditor({ jobId, initialMarkdown, llm, onSave, onClose }) {
  const [markdown, setMarkdown] = useState(initialMarkdown);
  const [instruction, setInstruction] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [history, setHistory] = useState([]);

  const handleSend = async () => {
    if (!instruction.trim()) return;
    setIsEditing(true);
    setHistory([...history, { role: 'user', text: instruction }]);
    const currentInstruction = instruction;
    setInstruction('');

    try {
      const res = await api.resumeChat({
        job_id: jobId,
        current_md: markdown,
        instruction: currentInstruction,
        llm: llm
      });
      setMarkdown(res.updated_md);
      setHistory(h => [...h, { role: 'agent', text: 'I have updated the resume based on your instruction.' }]);
    } catch (e) {
      setHistory(h => [...h, { role: 'agent', text: `Error: ${e.message}` }]);
    } finally {
      setIsEditing(false);
    }
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await api.resumeSave(jobId, { final_md: markdown, llm });
      if (onSave) onSave();
    } catch (e) {
      alert(e.message);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, 
      backgroundColor: 'var(--bg)', zIndex: 1000,
      display: 'flex', flexDirection: 'column'
    }}>
      <div style={{ padding: '1rem', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ margin: 0 }}>Live Resume Editor</h3>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button className="btn btn-outline" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleSave} disabled={isSaving}>
            {isSaving ? 'Saving...' : 'Save & Generate PDF'}
          </button>
        </div>
      </div>
      
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left Pane: Chat */}
        <div style={{ width: '350px', borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', background: 'var(--surface)' }}>
          <div style={{ flex: 1, overflowY: 'auto', padding: '1rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {history.length === 0 && (
              <div style={{ padding: '1rem', textAlign: 'center', color: 'var(--fg-muted)', fontSize: '0.875rem' }}>
                Give me an instruction to modify the resume. For example: "Make the summary shorter" or "Highlight my React experience."
              </div>
            )}
            {history.map((msg, i) => (
              <div key={i} style={{
                alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                backgroundColor: msg.role === 'user' ? 'var(--primary)' : 'var(--surface-2)',
                color: msg.role === 'user' ? '#fff' : 'var(--fg)',
                padding: '0.75rem 1rem', borderRadius: '1rem', maxWidth: '90%', fontSize: '0.875rem'
              }}>
                {msg.text}
              </div>
            ))}
            {isEditing && (
              <div style={{ alignSelf: 'flex-start', padding: '0.5rem', color: 'var(--fg-subtle)', fontSize: '0.875rem' }}>
                Agent is updating resume...
              </div>
            )}
          </div>
          <div style={{ padding: '1rem', borderTop: '1px solid var(--border)' }}>
            <textarea 
              value={instruction}
              onChange={e => setInstruction(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
              placeholder="e.g. 'Make the intro more aggressive' (Press Enter)"
              style={{ width: '100%', padding: '0.75rem', borderRadius: 'var(--radius)', border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--fg)', resize: 'none' }}
              rows={3}
              disabled={isEditing}
            />
            <button className="btn btn-primary" onClick={handleSend} disabled={isEditing || !instruction.trim()} style={{ width: '100%', marginTop: '0.5rem' }}>
              Send Instruction
            </button>
          </div>
        </div>
        
        {/* Right Pane: Live Rendered Resume */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '2rem', backgroundColor: '#fff', color: '#000' }}>
          <div style={{ maxWidth: '800px', margin: '0 auto', fontFamily: 'Arial, sans-serif' }}>
            <ReactMarkdown
              components={{
                h1: ({node, ...props}) => <h1 style={{ textAlign: 'center', fontSize: '24pt', marginBottom: '0.5rem' }} {...props} />,
                h2: ({node, ...props}) => <h2 style={{ fontSize: '14pt', borderBottom: '1px solid #ccc', paddingBottom: '0.2rem', marginTop: '1.5rem', color: '#2E74B5', fontWeight: 'bold' }} {...props} />,
                h3: ({node, ...props}) => {
                  const txt = props.children?.toString() || '';
                  if (txt.includes('||')) {
                    const [left, right] = txt.split('||');
                    return (
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '1rem', marginBottom: '0.2rem', fontWeight: 'bold' }}>
                        <span style={{ fontSize: '12pt' }}>{left.trim()}</span>
                        <span style={{ fontSize: '11pt' }}>{right.trim()}</span>
                      </div>
                    );
                  }
                  return <h3 style={{ fontSize: '12pt', marginTop: '1rem', marginBottom: '0.2rem' }} {...props} />;
                },
                p: ({node, ...props}) => {
                  const txt = props.children?.toString() || '';
                  if (txt.includes(' | ')) {
                    return <p style={{ textAlign: 'center', fontSize: '10pt', marginBottom: '1.5rem', color: '#444' }} {...props} />;
                  }
                  return <p style={{ margin: '0.2rem 0', fontSize: '10.5pt' }} {...props} />;
                },
                ul: ({node, ...props}) => <ul style={{ margin: '0.2rem 0 0 1.5rem', padding: 0 }} {...props} />,
                li: ({node, ...props}) => <li style={{ margin: '0.2rem 0', fontSize: '10.5pt' }} {...props} />,
                strong: ({node, ...props}) => <strong style={{ fontWeight: 600 }} {...props} />,
                em: ({node, ...props}) => <em style={{ fontStyle: 'italic', color: '#444' }} {...props} />
              }}
            >
              {markdown}
            </ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
}
