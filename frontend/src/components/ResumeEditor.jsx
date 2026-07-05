import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import { api } from '../api/client';

export default function ResumeEditor({ jobId, initialMarkdown, llm, onSave, onClose }) {
  const [markdown, setMarkdown] = useState(initialMarkdown);
  const [instruction, setInstruction] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [history, setHistory] = useState([]);
  const [pdfStyle, setPdfStyle] = useState({});

  // Load the user's PDF style so this preview matches the generated PDF exactly.
  useEffect(() => {
    api.getSettings()
      .then(s => { try { setPdfStyle(JSON.parse(s.resume_pdf_style || '{}')) } catch {} })
      .catch(() => {});
  }, []);

  // Mirror doc_generator._resume_md_to_html defaults.
  const fontFamily = pdfStyle.fontFamily || 'Cambria, Georgia, serif';
  const fs = parseFloat(pdfStyle.fontSize || '10.5');          // base pt
  const sectionColor = pdfStyle.sectionColor || '#2E74B5';
  const pt = n => `${n}pt`;

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
      <div style={{ padding: '1rem', borderBottom: '1px solid var(--surface-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
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
        <div style={{ width: '350px', borderRight: '1px solid var(--surface-border)', display: 'flex', flexDirection: 'column', background: 'var(--surface)' }}>
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
                color: msg.role === 'user' ? 'var(--primary-fg)' : 'var(--fg)',
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
          <div style={{ padding: '1rem', borderTop: '1px solid var(--surface-border)' }}>
            <textarea
              value={instruction}
              onChange={e => setInstruction(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
              placeholder="e.g. 'Make the intro more aggressive' (Press Enter)"
              style={{ width: '100%', padding: '0.75rem', borderRadius: 'var(--radius)', border: '1px solid var(--surface-border)', background: 'var(--bg)', color: 'var(--fg)', resize: 'none' }}
              rows={3}
              disabled={isEditing}
            />
            <button className="btn btn-primary" onClick={handleSend} disabled={isEditing || !instruction.trim()} style={{ width: '100%', marginTop: '0.5rem' }}>
              Send Instruction
            </button>
          </div>
        </div>
        
        {/* Right Pane: Live Rendered Resume — mirrors the generated PDF's CSS */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '2rem', backgroundColor: 'var(--bg-2)' }}>
          <div style={{
            maxWidth: '850px', margin: '0 auto', fontFamily,
            backgroundColor: '#fff', color: '#111', padding: '40px 60px',
            boxShadow: 'var(--shadow-lg)', borderRadius: '4px',
            minHeight: '1000px', lineHeight: '1.45', fontSize: pt(fs)
          }}>
            <ReactMarkdown
              components={{
                h1: ({node, ...props}) => <h1 style={{ textAlign: 'center', fontSize: pt(fs + 10), margin: '0 0 4px 0', fontWeight: 700, color: '#000' }} {...props} />,
                h2: ({node, ...props}) => <h2 style={{ fontSize: pt(fs), fontWeight: 700, color: sectionColor, textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: `1.5px solid ${sectionColor}`, paddingBottom: 2, margin: '12px 0 5px 0' }} {...props} />,
                h3: ({node, ...props}) => {
                  const txt = props.children?.toString() || '';
                  if (txt.includes('||')) {
                    const [left, right] = txt.split('||');
                    return (
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: '7px', marginBottom: '1px' }}>
                        <span style={{ fontSize: pt(fs), color: '#000', fontWeight: 700 }}>{left.trim()}</span>
                        <span style={{ fontSize: pt(fs - 1), color: '#333', fontStyle: 'italic' }}>{right.trim()}</span>
                      </div>
                    );
                  }
                  return <h3 style={{ fontSize: pt(fs), marginTop: '7px', marginBottom: '1px', color: '#000', fontWeight: 700 }} {...props} />;
                },
                p: ({node, ...props}) => {
                  const txt = props.children?.toString() || '';
                  if (txt.includes(' | ')) {
                    return <p style={{ textAlign: 'center', fontSize: pt(fs - 1), margin: '0 0 10px 0', color: '#444' }} {...props} />;
                  }
                  return <p style={{ margin: '3px 0', fontSize: pt(fs), color: '#111' }} {...props} />;
                },
                ul: ({node, ...props}) => <ul style={{ margin: '3px 0 5px 18px', padding: 0 }} {...props} />,
                li: ({node, ...props}) => <li style={{ marginBottom: '2px', fontSize: pt(fs - 0.5), color: '#111' }} {...props} />,
                strong: ({node, ...props}) => <strong style={{ fontWeight: 700 }} {...props} />,
                em: ({node, ...props}) => <em style={{ fontStyle: 'italic' }} {...props} />
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
