"""Deprecated unsafe finalizer.  Derived evaluations have their own runner."""
import argparse

def main():
 p=argparse.ArgumentParser(); p.add_argument('--db',required=True); p.parse_args()
 raise RuntimeError('legacy finalize is disabled; use derived evaluation lineage')
if __name__=='__main__': main()
