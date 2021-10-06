# -*- coding: utf-8 -*-
"""
Created on Sat Sep 25 12:14:33 2021

@author: wangl
"""

import re


with open('question_have_can.de.dev') as f:
    s = f.read()

s = re.sub(' +\. +', '\t', s)
s = re.sub(' +\.','',s)

with open('val.pt','w') as f:
    f.write(s)

#--------------------------------------------------

with open('question_have_can.de.test') as f:
    s = f.read()

s = re.sub(' +\. +', '\t', s)
s = re.sub(' +\.','',s)

with open('test.pt','w') as f:
    f.write(s)
    
#--------------------------------------------------
    
with open('question_have_can.de.train') as f:
    s = f.read()

s = re.sub(' +\. +', '\t', s)
s = re.sub(' +\.','',s)

with open('train.pt','w') as f:
    f.write(s)
    
#--------------------------------------------------   
    
with open('question_have_can.de.gen') as f:
    s = f.read()

s = re.sub(' +\. +', '\t', s)
s = re.sub(' +\.','',s)

with open('gen.pt','w') as f:
    f.write(s)