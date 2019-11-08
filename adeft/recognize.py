"""Implements the disambiguation of shortforms based on recognizing an
explicit defining pattern in text."""

import re
import string
import logging

from nltk.stem.snowball import EnglishStemmer

from adeft.nlp import tokenize, untokenize
from adeft.util import get_candidate_fragments, get_candidate

logger = logging.getLogger(__file__)

try:
    from adeft.score import AdeftLongformScorer
except Exception:
    logger.info('OneShotRecognizer not available. AdeftLongformScorer'
                ' has not been built successfully.')

_stemmer = EnglishStemmer()


class BaseRecognizer(object):
    """Base class for recognizers

    Recognizers are built to identify longform expansions for a shortform by
    searching for defining patterns (DPs).

    Parameters
    ----------
    shortform : str
        shortform to be recognized
    window : Optional[int]
        Specifies range of characters before a defining pattern (DP)
        to consider when finding longforms. Should be set to the same value
        that was used in the AdeftMiner that was used to find longforms.
        Default: 100
    exclude : Optional[set]
        set of tokens to ignore when searching for longforms.
        Default: None
    """
    def __init__(self, shortform, window=100, exclude=None):
        self.shortform = shortform
        self.window = window
        if exclude is None:
            self.exclude = set([])
        else:
            self.exclude = exclude

    def recognize(self, text):
        """Find longforms in text by searching for defining patterns (DPs)

        Parameters
        ----------
        text : str
            Sentence where we seek to disambiguate shortform

        Returns
        -------
        expansions : set of str
            Set of longforms corresponding to shortform in sentence if a
            defining pattern is matched. Returns None if no defining patterns
            are found
        """
        expansions = set()
        fragments = get_candidate_fragments(text, self.shortform,
                                            window=self.window)
        for fragment in fragments:
            if not fragment:
                continue
            tokens = get_candidate(fragment, self.exclude)
            # search for longform in trie
            longform = self._search(tokens)
            # if a longform is recognized, add it to output list
            if longform:
                expansion = self._post_process(longform)
                expansions.add(expansion)
        return expansions

    def strip_defining_patterns(self, text):
        """Return text with defining patterns stripped

       This is useful for training machine learning models where training
       labels are generated by finding defining patterns (DP)s. Models must
       be trained to disambiguate texts that do not contain a defining
       pattern.

       The output on the first sentence of the previous paragraph is
       "This is useful for training machine learning models where training
       labels are generated by finding DPs."

       Parameters
       ----------
       text : str
           Text to remove defining patterns from

       Returns
       -------
       stripped_text : str
           Text with defining patterns replaced with shortform
        """
        fragments = get_candidate_fragments(text, self.shortform)
        for fragment in fragments:
            # Each fragment is tokenized and its longform is identified
            tokens = tokenize(fragment)
            longform = self._search([token for token, _ in tokens
                                     if token not in string.punctuation])
            if longform is None:
                # For now, ignore a fragment if its grounding has no longform
                # from the grounding map
                continue
            # Remove the longform from the fragment, keeping in mind that
            # punctuation is ignored when extracting longforms from text
            num_words = len(longform.split())
            i = 0
            j = len(tokens) - 1
            while i < num_words:
                if re.match(r'\w+', tokens[j][0]):
                    i += 1
                j -= 1
                if i > 100:
                    break
            text = text.replace(fragment.strip(),
                                untokenize(tokens[:j+1]))
        # replace all instances of parenthesized shortform with shortform
        stripped_text = re.sub(r'\(\s*%s\s*\)'
                               % self.shortform,
                               ' ' + self.shortform + ' ', text)
        stripped_text = ' '.join(stripped_text.split())
        return stripped_text

    def _search(self, tokens):
        """Method to identify longform expansion from tokens preceeding DP

        This method should take a list of tokens preceeding a defining pattern
        and return a longform expansion as a single string
        """
        raise NotImplementedError

    def _post_process(self, text):
        """Post-processing step for longform expansion

        Default to no post-processing
        """
        return text


class _TrieNode(object):
    """TrieNode structure for use in recognizer

    Attributes
    ----------
    longform : str or None
        Set to associated longform at leaf nodes in the trie, otherwise None.
        Each longform corresponds to a path in the trie from root to leaf.

    children : dict
        dict mapping tokens to child nodes
    """
    __slots__ = ['longform', 'children']

    def __init__(self, longform=None):
        self.longform = longform
        self.children = {}


class AdeftRecognizer(BaseRecognizer):
    """Class for recognizing longforms by searching for defining patterns (DP)

    Searches text for the pattern "<longform> (<shortform>)" for a collection
    of grounded longforms supplied by the user.

    Parameters
    ----------
    shortform : str
        shortform to be recognized
    grounding_map : dict[str, str]
        Dictionary mapping longform texts to their groundings
    window : Optional[int]
        Specifies range of characters before a defining pattern (DP)
        to consider when finding longforms. Should be set to the same value
        that was used in the AdeftMiner that was used to find longforms.
        Default: 100
    exclude : Optional[set]
        set of tokens to ignore when searching for longforms.
        Default: None

    Attributes
    ----------
    _trie : :py:class:`adeft.recognize._TrieNode`
        Trie used to search for longforms. Edges correspond to stemmed tokens
        from longforms. They appear in reverse order to the bottom of the trie
        with terminal nodes containing the associated longform in their data.
    """
    def __init__(self, shortform, grounding_map, window=100, exclude=None):
        self.grounding_map = grounding_map
        self._trie = self._init_trie()
        super().__init__(shortform, window, exclude)

    def _init_trie(self):
        """Initialize search trie with longforms in grounding map

        Returns
        -------
        root : :py:class:`adeft.recogize._TrieNode`
            Root of search trie used to recognize longforms
        """
        root = _TrieNode()
        for longform, grounding in self.grounding_map.items():
            edges = tuple(_stemmer.stem(token)
                          for token, _ in tokenize(longform))[::-1]
            current = root
            for index, token in enumerate(edges):
                if token not in current.children:
                    if index == len(edges) - 1:
                        new = _TrieNode(longform)
                    else:
                        new = _TrieNode()
                    current.children[token] = new
                    current = new
                else:
                    current = current.children[token]
        return root

    def _search(self, tokens):
        """Find longform expansion based on grounding map

        Parameters
        ----------
        tokens : list of str
            contains tokens that precede the occurence of the pattern
            "<longform> (<shortform>)" up until the start of the containing
            sentence or an excluded word is reached.

        Returns
        -------
        str
            Identified longform expansion
        """
        current = self._trie
        for token in tuple(_stemmer.stem(token) for token in tokens[::-1]):
            if token not in current.children:
                break
            if current.children[token].longform is None:
                current = current.children[token]
            else:
                return current.children[token].longform

    def _post_process(self, longform):
        """Map longform associated grounding in grounding map"""
        return self.grounding_map[longform]


class OneShotRecognizer(BaseRecognizer):
    """Identify longform expansions using subsequence matching

    Uses a string matching algorithm to determine longform boundaries
    for a defining pattern for only a single text.

    Attributes
    ----------
    shortform : str
        shortform to be recognized
    window : Optional[int]
        Specifies range of characters before a defining pattern (DP)
        to consider when finding longforms. Should be set to the same value
        that was used in the AdeftMiner that was used to find longforms.
        Default: 100
    exclude : Optional[set]
        set of tokens to ignore when searching for longforms.
        Default: None
    **params
        Parameters for :py:class`adeft.score.AdeftLongformScorer`
    """
    def __init__(self, shortform, window=100, exclude=None, **params):
        try:
            self.scorer = AdeftLongformScorer(shortform, **params)
        except NameError:
            logger.exception('OneShotRecognizer not available.'
                             ' AdeftLongformScorer has not been built'
                             ' successfully.')
        super().__init__(shortform, window, exclude)

    def _search(self, tokens):
        """Use AdeftLongformScorer to identify expansions"""
        result = self.scorer.score(tokens)
        return result[0]
