"""Source launcher with an absolute import path (also usable at Windows login)."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from nicoview.__main__ import main
if __name__=='__main__':main()
