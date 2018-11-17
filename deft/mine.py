import math
import string
from nltk.tokenize import word_tokenize, sent_tokenize
from deft.nlp.stem import SnowCounter
from deft import resources


class ContinuousMiner(object):
    __slots__ = ['shortform', '_internal_trie',
                 '_longforms', '_snow', 'exclude']
    """Finds possible longforms corresponding to an abbreviation in a text corpus

    An online method. Updates likelihoods of terms as it consumes
    additional texts. Based on a trie datastructure. Likelihoods for longforms
    are updated at the time of insertions into the trie.

    Parameters
    ----------
    shortform: str
        Search for candidate longforms associated to this shortform

    stop_words: Optional[set of str]
        Terms that are to be excluded from candidate longforms.
        Default: None

    Attributes
    ----------
    _internal_trie: :py:class:`deft.mine.ContinuousMiner.TrieNode`
        Stores trie datastructure that algorithm is based upon

    _longforms: dict
        Dictionary mapping candidate longforms to their likelihoods as
        produced by the acromine algorithm

    _snow: :py:class:`deft.nlp.stem.SnowCounter`
        English stemmer that keeps track of counts of the number of times a
        given word has been mapped to a given stem. Wraps the class
        EnglishStemmer from nltk.stem.snowball
    """
    def __init__(self, shortform, exclude=None):
        self.shortform = shortform
        self._internal_trie = self.TrieNode()
        self._longforms = {}
        self._snow = SnowCounter()
        if not exclude:
            exclude = set([])
        self.exclude = exclude

    class TrieNode(object):
        """ Node in Trie associated to a candidate longform

        The children of a node associated to a candidate longform c are all
        observed candidates t that can be obtained by prepending a single token
        to c.

        Contains the current likelihood for the candidate longform as well
        as all information needed to calculate it. The likelihood's are
        updated as candidates are added to the Trie.


        Parameters
        ----------
        longform: list of str
            List of tokens within a candidate longform in reverse order.
            For the candidate longform "in the endoplasmic reticulum" the
            list will take the form ["reticulum", "endoplasmic", "the", "in"],
            ignoring that the tokens should actually be stemmed.

        Attributes
        ----------

        longform: list of str
            Explained above

        count: int
            Current co-occurence frequency of candidate longform with shortform

        sum_ft: int
            Sum of the co-occurence frequencies of all previously observed
            candidate longforms that are children of the associated candidate
            longform (longforms that can be obtained by prepending
            one token to the associated candidate longform).

        sum_ft2: int
            Sum of the squares of the co-occurence freqencies of all previously
            observed candidate longforms that are children of the associated
            longform.
        LH: float
            Likelihood of the associated candidate longform. It is given by
            count*log(len(longform)) - sum_ft/sum_ft**2
            See

            [Okazaki06] Naoaki Okazaki and Sophia Ananiadou. "Building an
            abbreviation dicationary using a term recognition approach".
            Bioinformatics. 2006. Oxford University Press.

            for more information

        children: dict of :py:class:`deft.mine.ContinuousMiner.TrieNode`
            dictionary of child nodes
        """
        __slots__ = ['longform', 'count', 'sum_ft', 'sum_ft2', 'LH',
                     '__length_penalty', 'children']
        """DocString
        """
        def __init__(self, longform=()):
            self.longform = longform
            if longform:
                self.count = 1
                self.sum_ft = self.sum_ft2 = 0
                self.LH = self.__length_penalty = math.log(len(longform))
            self.children = {}

        def increment_count(self):
            """Update count and likelihood when observing a longform again"""
            self.count += 1
            self.LH += self.__length_penalty

        def update_likelihood(self, count):
            """Update likelihood when observing a child of associated longform

            Update when observing a candidate longform which can be obtained
            by prepending one token to the associated longform.

            Parameters
            ----------
            count: int
                Current co-occurence frequency of child longform with shortform
            """
            self.LH += self.sum_ft/self.sum_ft2 if self.sum_ft2 else 0
            self.sum_ft += 1
            self.sum_ft2 += 2*count - 1
            self.LH -= self.sum_ft/self.sum_ft2

    def consume(self, texts):
        """Consume a corpus of texts and use them to train the miner

        Each text is tokenized into sentences. Sentences are identified that
        contain the pattern f'({self.shortform}). These sentences are then
        tokenized into lists of words and punctuation is removed from these
        lists. The remaining words are then stemmed and converted to lower
        case. A maximal candidate longform is found for each of these
        sentences. Every suffix is then considered as a candidate longform and
        added to the internal trie, which updates the likelihoods for each of
        the candidates.

        If there are no excluded stop words, for the sentence "The growth of
        estrogen receptor (ER)-positive breast cancer cells is inhibited by
        all-trans-retinoic acid (RA)." The maximal candidate longform would be
        "the growth of estrogen receptor". It and the candidates
        "growth of estrogen receptor", "of estrogen receptor",
        "estrogen receptor", "receptor" would then be added to the internal
        trie.

        Parameters
        ----------
        texts: list of str
            A list of texts
        """
        # split each text into a list of sentences
        split = [sent_tokenize(text) for text in texts]

        # extract sentences defining shortform through standard pattern
        sentences = [sentence for text in split for sentence in text
                     if f'({self.shortform})' in sentence]

        # tokenize these sentences into lists of words
        tokens = [word_tokenize(sentence) for sentence in sentences]

        # extract maximal candidate longforms from each such sentence
        candidates = [self._get_candidates(sentence)
                      for sentence in tokens]

        # add each candidate to the internal trie
        for candidate in candidates:
            self._add(candidate)

    def top(self, limit=None):
        """Return top scoring candidates from the mine.

        Parameters
        ----------
        limit: Optional[int]
            Limit for the number of candidates to return. Default: None

        Returns
        ------
        candidates: list of tuple
            List of tuples, each containing a candidate string and its
            likelihood score, sorted in descending order by likelihood score.
        """
        if not self._longforms:
            return []

        candidates = sorted(self._longforms.items(), key=lambda x: x[1],
                            reverse=True)
        if limit is not None and limit < len(candidates):
            candidates = candidates[0:limit]

        candidates = [(' '.join(self._snow.most_frequent(token)
                                for token in longform),
                       LH)
                      for longform, LH in candidates]
        return candidates

    def _add(self, tokens):
        """Add a list of tokens to the internal trie and update likelihoods.

        Parameters
        ----------
        tokens: str
            A list of tokens to add to the internal trie.

        """
        # start at top of trie
        current = self._internal_trie
        # apply snowball stemmer to each token
        tokens = [self._snow.stem(token) for token in tokens]
        # tokens must be inserted into the trie in reverse order
        tokens = tokens[::-1]
        for token in tokens:
            if token not in current.children:
                # candidate longform is observed for the first time
                # add a new entry for it in the trie
                longform = current.longform + (token, )
                new = self.TrieNode(longform)
                # Add newly observed longform to the dictionary of candidates
                self._longforms[new.longform[::-1]] = new.LH
                # set newly observed longform to be child of current node in
                # trie
                current.children[token] = new
                # update current node to the newly formed node
                current = new
            else:
                # candidate longform has been observed before
                # update count for candidate longform and associated LH value
                current.children[token].increment_count()
                # Update entry for candidate longform in the candidates
                # dictionary
                self._longforms[current.children[token].longform[::-1]] = \
                    current.children[token].LH
                if current.longform:
                    # we are not at the top of the trie. observed candidate
                    # has a parent

                    # update likelihood of candidate's parent
                    count = current.children[token].count
                    current.update_likelihood(count)
                    # Update candidates dictionary
                    self._longforms[current.longform[::-1]] = current.LH
                current = current.children[token]

    def _get_candidates(self, tokens):
        """Returns maximal candidate longform from a list of tokens.

        Parameters
        ----------
        tokens: list of str
            A list of tokens which that been taken from a sentence containing
            the shortform in parentheses

        Returns
        -------
        candidate: list of str
            Sublist of input list containing tokens between start of sentence
            and first occurence of the shortform in parentheses, or between
            a stop word and the first occurence of the shortform in parentheses
            if there is a set of stop words to exclude from longforms.
        """
        # Loop through tokens. The nltk word tokenizer used will split off
        # the parentheses surrounding the shortform into separate tokens.
        for index in range(len(tokens) - 3):
            if tokens[index] == '(' and tokens[index+1] == self.shortform and \
               tokens[index+2] == ')':
                # The shortform has been found in parentheses

                # Capture all tokens in the sentence up until but excluding
                # the left parenthese containing the shortform, excluding
                # punctuation
                candidate = [token for token in tokens[:index]
                             if token not in string.punctuation]
                # If a token consists of a sole unicode greek letter, replace
                # it with the letter spelled out in Roman characters
                for i, token in enumerate(candidate):
                    if token in resources.greek_alphabet:
                        candidate[i] = resources.greek_alphabet[token]
                # convert tokens to lower case
                candidate = [token.lower() for token in candidate]
                # Keep only the tokens preceding the left parenthese up until
                # but not including the first stop word
                i = len(candidate)-1
                while i >= 0 and candidate[i] not in self.exclude:
                    print(candidate[i])
                    i -= 1
                candidate = candidate[i+1:]
                return candidate