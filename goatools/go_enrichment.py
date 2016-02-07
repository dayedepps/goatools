#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
python %prog study.file population.file gene-association.file

This program returns P-values for functional enrichment in a cluster of
study genes using Fisher's exact test, and corrected for multiple testing
(including Bonferroni, Holm, Sidak, and false discovery rate)
"""

from __future__ import absolute_import

__copyright__ = "Copyright (C) 2010-2016, H Tang et al., All rights reserved."
__author__ = "various"

import sys
import fisher
import collections as cx

from .multiple_testing import Bonferroni, Sidak, HolmBonferroni, FDR, calc_qval
from .ratio import get_terms, count_terms, is_ratio_different
import goatools.wr_tbl as RPT


class GOEnrichmentRecord(object):
    """Represents one result (from a single GOTerm) in the GOEnrichmentStudy
    """
    namespace2NS = cx.OrderedDict([
        ('biological_process', 'BP'),
        ('molecular_function', 'MF'),
        ('cellular_component', 'CC')])

    # Fields seen in every enrichment result
    _fldsdefprt = ["GO", "NS", "enrichment", "name", "ratio_in_study", "ratio_in_pop", "p_uncorrected"]
    _fldsdeffmt = ["%2s"] + ["%s"] * 3 + ["%d/%d"] * 2 + ["%.3g"]

    _flds = set(_fldsdefprt).intersection(
            set(['study_items', 'study_count', 'study_n', 'pop_items', 'pop_count', 'pop_n']))

    def __init__(self, **kwargs):
        # Methods seen in current enrichment result
        self._methods = [] 
        for k, v in kwargs.items():
            setattr(self, k, v)
            if k == 'ratio_in_study':
                setattr(self, 'study_count', v[0])
                setattr(self, 'study_n', v[1])
            if k == 'ratio_in_pop':
                setattr(self, 'pop_count', v[0])
                setattr(self, 'pop_n', v[1])
        self._init_enrichment()
        self.goterm = None  # the reference to the GOTerm

    def get_prtflds_default(self):
        """Get default fields."""
        return self._fldsdefprt + ["p_{M}".format(M=m) for m in self._methods]

    def set_corrected_pval(self, method, pvalue):
        self._methods.append(method)
        setattr(self, "".join(["p_", method]), pvalue)

    def __str__(self, indent=False):
        field_data = [getattr(self, f, "n.a.") for f in self._fldsdefprt] + \
                     [getattr(self, "p_{}".format(m)) for m in self._methods]
        field_formatter = self._fldsdeffmt + ["%.3g"]*len(self._methods)
        assert len(field_data) == len(field_formatter)

        # default formatting only works for non-"n.a" data
        for i, f in enumerate(field_data):
            if f == "n.a.":
                field_formatter[i] = "%s"

        # print dots to show the level of the term
        dots = ""
        if self.goterm is not None and indent:
            dots = "." * self.goterm.level

        prtdata = "\t".join(a % b for (a, b) in zip(field_formatter, field_data))
        return "".join([dots, prtdata])

    def __repr__(self):
        return "GOEnrichmentRecord({GO})".format(GO=self.GO)

    def set_goterm(self, go):
        self.goterm = go.get(self.GO, None)
        present = self.goterm is not None
        self.name = self.goterm.name if present else "n.a."
        self.NS = self.namespace2NS[self.goterm.namespace] if present else "XX"

    def _init_enrichment(self):
        """Mark as 'enriched' or 'purified'."""
        self.enrichment = 'e' if ((1.0 * self.study_count / self.study_n) >
                                  (1.0 * self.pop_count / self.pop_n)) else 'p'

    def update_remaining_fldsdefprt(self, min_ratio=None):
        self.is_ratio_different = is_ratio_different(min_ratio, self.study_count,
                                                     self.study_n, self.pop_count, self.pop_n)


class GOEnrichmentStudy(object):
    """Runs Fisher's exact test, as well as multiple corrections
    """
    all_methods = ("bonferroni", "sidak", "holm", "fdr")

    # Default Excel table column widths 
    default_fld2col_widths = {
        'NS'        :  3,
        'GO'        : 12,
        'level'     :  3,
        'enrichment':  1,
        'name'      : 60,
        'ratio_in_study':  8,
        'ratio_in_pop'  : 12,
    }

    def __init__(self, pop, assoc, obo_dag, propagate_counts=True,
                 alpha=.05,
                 methods=["bonferroni", "sidak", "holm"]):

        self.pop = pop
        self.pop_n = len(pop)
        self.assoc = assoc
        self.obo_dag = obo_dag
        self.alpha = alpha
        self.methods = methods

        if propagate_counts:
            print >> sys.stderr, "Propagating term counts to parents .."
            obo_dag.update_association(assoc)
        self.go2popitems = get_terms(pop, assoc, obo_dag)

    def run_study(self, study, **kws):
        """Run Gene Ontology Enrichment Study (GOEA) on study ids."""
        # Calculate uncorrected pvalues
        results = self._get_pval_uncorr(study)

        # Do multipletest corrections on uncorrected pvalues and update results
        methods = kws['methods'] if 'methods' in kws else self.methods
        alpha = kws['alpha'] if 'alpha' in kws else self.alpha
        self._run_multitest_corr(results, methods, alpha, study)


        for rec in results:
            # get go term for name and level
            rec.set_goterm(self.obo_dag)

        # Default sort order: 1st sort by BP, MF, CC. 2nd sort by pval
        results.sort(key=lambda r: [r.NS, r.p_uncorrected])

        return results

    def _get_pval_uncorr(self, study):
        """Calculate the uncorrected pvalues for study items."""
        results = []
        go2studyitems = get_terms(study, self.assoc, self.obo_dag)
        pop_n, study_n = self.pop_n, len(study)
        allterms = set(go2studyitems.keys() + self.go2popitems.keys())

        for term in allterms:
            study_items = go2studyitems.get(term, set())
            study_count = len(study_items)
            pop_items = self.go2popitems.get(term, set())
            pop_count = len(pop_items)
            p = fisher.pvalue_population(study_count, study_n,
                                         pop_count, pop_n)

            one_record = GOEnrichmentRecord(
                GO=term,
                p_uncorrected=p.two_tail,
                study_items=study_items,
                pop_items=pop_items,
                ratio_in_study=(study_count, study_n),
                ratio_in_pop=(pop_count, pop_n))

            results.append(one_record)
          
        return results
        
    def _run_multitest_corr(self, results, methods, alpha, study):
        """Do multiple-test corrections on uncorrected pvalues."""
        assert 0 < alpha < 1, "Test-wise alpha must fall between (0, 1)"
        pvals = [r.p_uncorrected for r in results]
        bonferroni, sidak, holm, fdr = None, None, None, None

        for method in methods:
            if method == "bonferroni":
                bonferroni = Bonferroni(pvals, alpha).corrected_pvals
            elif method == "sidak":
                sidak = Sidak(pvals, alpha).corrected_pvals
            elif method == "holm":
                holm = HolmBonferroni(pvals, alpha).corrected_pvals
            elif method == "fdr":
                # get the empirical p-value distributions for FDR
                term_pop = getattr(self, 'term_pop', None)
                if term_pop is None:
                    term_pop = count_terms(self.pop, self.assoc, self.obo_dag) 
                p_val_distribution = calc_qval(len(study),
                                               self.pop_n,
                                               self.pop, self.assoc,
                                               term_pop, self.obo_dag)
                fdr = FDR(p_val_distribution,
                          results, alpha).corrected_pvals
            else:
                raise Exception("INVALID METHOD({MX}). VALID METHODS: {Ms}".format(
                                MX=method, Ms=" ".join(self.all_methods)))

        all_corrections = (bonferroni, sidak, holm, fdr)

        for method, corrected_pvals in zip(self.all_methods, all_corrections):
            self._update_results(results, method, corrected_pvals)

    # Methods for writing results into tables: text, tab-separated, Excel spreadsheets
    def prt_txt(self, prt, results_nt, prtfmt, **kws):
        """Print GOEA results in text format."""
        prt_flds = RPT.get_fmtflds(prtfmt)
        data_nts = self._get_nts(results_nt, prt_flds, **kws)
        RPT.prt_txt(prt, data_nts, prtfmt, prt_flds, **kws)

    def wr_xlsx(self, fout_xlsx, results_nt, **kws):
        """Write a xlsx file."""
        prt_flds = kws['prt_flds'] if 'prt_flds' in kws else self.get_prtflds_default(results_nt)
        xlsx_data = self._get_nts(results_nt, prt_flds, **kws)
        if 'fld2col_widths' not in kws:
            kws['fld2col_widths'] = {f:self.default_fld2col_widths.get(f, 8) for f in prt_flds}
        RPT.wr_xlsx(fout_xlsx, xlsx_data, **kws)

    def wr_tsv(self, fout_tsv, results_nt, **kws):
        """Write tab-separated table data to file"""
        prt_flds = kws['prt_flds'] if 'prt_flds' in kws else self.get_prtflds_default(results_nt)
        tsv_data = self._get_nts(results_nt, prt_flds, **kws)
        RPT.wr_tsv(fout_tsv, tsv_data, prt_flds, **kws)

    def prt_tsv(self, prt, results_nt, **kws):
        """Write tab-separated table data"""
        prt_flds = kws['prt_flds'] if 'prt_flds' in kws else self.get_prtflds_default(results_nt)
        tsv_data = self._get_nts(results_nt, prt_flds, **kws)
        RPT.prt_tsv(prt, tsv_data, prt_flds, **kws)

    def _get_nts(self, results, fldnames, **kws):
        """Get namedtuples containing user-specified (or default) data from GOEA results.

            Reformats data from GOEnrichmentRecord objects into lists of nts
            So that generic table writer's may be used.
        """
        keep_if = None if 'keep_if' not in kws else kws['keep_if']
        data_nts = [] # A list of namedtuples containing GOEA results
        NtGoeaResults = cx.namedtuple("NtGoeaResults", " ".join(fldnames))
        # Loop through GOEA results stored in a GOEnrichmentRecord object
        for goerec in results:
            row = []
            # Loop through each user field desired
            for fld in fldnames:
                # 1. Check the GOEnrichmentRecord's attributes
                val = getattr(goerec, fld, None)
                if val is not None:
                    if fld.startswith("ratio_"):
                        val = "{N}/{TOT}".format(N=val[0], TOT=val[1])
                    row.append(val)
                else:
                    # 2. Check the GO object for the field
                    val = getattr(goerec.goterm, fld, None)
                    if val is not None:
                        row.append(val)
                    else:
                        # 3. Field not found, raise Exception
                        chk = self._err_fld(goerec, fld, fldnames, row) 
            nt = NtGoeaResults._make(row)
            if keep_if is None or keep_if(nt):
                data_nts.append(nt)
        return data_nts

    def _err_fld(self, goerec, fld, fldnames, row):
        """Unrecognized field. Print detailed Failure message."""
        msg = ['ERROR. UNRECOGNIZED FIELD({F})'.format(F=fld)]
        actual_flds = set(goerec.get_prtflds_default() + goerec.goterm.__dict__.keys())
        bad_flds = set(fldnames).difference(set(actual_flds))
        if bad_flds:
            msg.append("\nGOEA RESULT FIELDS: {}".format(" ".join(goerec._fldsdefprt)))
            msg.append("GO FIELDS: {}".format(" ".join(goerec.goterm.__dict__.keys())))
            msg.append("\nFATAL: {N} UNEXPECTED FIELDS({F})\n".format(N=len(bad_flds), F=" ".join(bad_flds)))
            msg.append("  {N} User-provided fields:".format(N=len(fldnames)))
            for idx, fld in enumerate(fldnames, 1):
              mrk = "ERROR -->" if fld in bad_flds else ""
              msg.append("  {M:>9} {I:>2}) {F}".format(M=mrk, I=idx, F=fld))
        raise Exception("\n".join(msg))
  
    @staticmethod
    def get_prtflds_default(results):
        """Get default fields names. Used in printing GOEA results.

           Researchers can control which fields they want to print in the GOEA results
           or they can use the default fields.
        """
        if results:
          return results[0].get_prtflds_default()
        return []

    @staticmethod
    def _update_results(results, method, corrected_pvals):
        """Add data members to store multiple test corrections."""
        if corrected_pvals is None:
            return
        for rec, val in zip(results, corrected_pvals):
            rec.set_corrected_pval(method, val)

    @staticmethod
    def print_summary(results, min_ratio=None, indent=False, pval=0.05):
        from .version import __version__ as version
        from datetime import date

        # Header contains provenance and parameters
        print("# Generated by GOATOOLS v{0} ({1})".format(version, date.today()))
        print("# min_ratio={0} pval={1}".format(min_ratio, pval))

        # field names for output
        if results:
            print("\t".join(GOEnrichmentStudy.get_prtflds_default(results)))

        for rec in results:
            # calculate some additional statistics
            # (over_under, is_ratio_different)
            rec.update_remaining_fldsdefprt(min_ratio=min_ratio)

            if pval is not None and rec.p_uncorrected >= pval:
                continue

            if rec.is_ratio_different:
                print(rec.__str__(indent=indent))

# Copyright (C) 2010-2016, H Tang et al., All rights reserved.
