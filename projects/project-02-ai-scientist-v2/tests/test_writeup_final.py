"""Exercise the actual final-reflection block without model or TeX dependencies."""
import ast
import os.path as osp
from pathlib import Path
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'ai_scientist/perform_icbinb_writeup.py'

class FinalReflectionTests(unittest.TestCase):
    def test_current_context_and_no_change_pdf(self):
        tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='perform_writeup')
        body=next(n for n in function.body if isinstance(n,ast.Try)).body
        index=next(i for i,n in enumerate(body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='final_reflection_prompt' for t in n.targets))
        end=next(i for i in range(index,len(body)) if isinstance(body[i],ast.Assign) and any(isinstance(t,ast.Name) and t.id=='reflection_code_match' for t in body[i].targets))
        block=ast.Module(body=body[index-1:end],type_ignores=[])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            paper=Path(directory)/'template.tex'
            paper.write_text('LATEST VERIFIED PAPER: 48 cached answers',encoding='utf-8')
            captured={}
            def respond(**kwargs):
                captured.update(kwargs)
                return 'I am done', []
            def compile_pdf(folder,path):
                Path(path).write_bytes(b'test compile sentinel')
            namespace=dict(writeup_file=str(paper),reflection_page_info='Main text: 3 pages',idea_text='Prompt injection pilot',big_client=None,big_client_model='test',big_model_system_message='test',msg_history=[{'role':'assistant','content':'I am done'}],base_folder=directory,latex_folder=directory,i=0,osp=osp,get_response_from_llm=respond,compile_latex=compile_pdf)
            exec(compile(block,str(SOURCE),'exec'),namespace)
            self.assertIn('Main text: 3 pages',captured['prompt'])
            self.assertIn('LATEST VERIFIED PAPER: 48 cached answers',captured['prompt'])
            self.assertIn('Prompt injection pilot',captured['prompt'])
            self.assertNotIn('{reflection_page_info}',captured['prompt'])
            self.assertTrue(Path(namespace['reflection_pdf']).exists())

if __name__=='__main__':
    unittest.main()
