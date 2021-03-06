# -*- coding: utf-8 -*-
"""
Created on Thu Sep 14 19:13:58 2017

@author: papen
"""

import quantities as pq
import load_data as ld
import analyze_data as ad
import matplotlib.pyplot as plt

# set path
datadir = '/home/papen/sciebo/RestingState/nikos2RSgivenData/'
class_file = './nikos2rs_consistency_EIw035complexc04.txt'

# set parameters
binsize  = 150*pq.ms
nbins    = 60
binrange = [-0.4, 0.4]
eiThres  = 0.4

# load data
sts = ld.load_nikos2rs(path2file  = datadir, 
                       class_file = class_file,
                       eiThres    = eiThres)

# calculate pdf of covariances
pdf, bins, C = ad.covariance_analysis(sts, 
                                      binsize  = binsize,
                                      nbins    = nbins,
                                      binrange = binrange)

# plot results
ntypes = pdf.keys()
for nty in ntypes:
    plt.plot(bins,pdf[nty], label=nty)
plt.legend()
plt.show()