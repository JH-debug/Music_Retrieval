import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.utils.data import default_collate

from audio.style_encoder import StyleEncoder
from audio.dataloader import MelDataset
from multi_dataloader import AudioTextDataset
from text.model import TextEncoder
from text.dataloader import TextClassification_Dataset
from sklearn.metrics.pairwise import *

from word2vec import W2V

import argparse
from tqdm import tqdm

from tensorboardX import SummaryWriter


def create_data_loader(modal, data_dir, split, batch_size, num_workers, **kwargs):
    if modal == 'text':
        dataset = TextClassification_Dataset(data_dir, split, max_len=kwargs['max_len'])
    else:
        if split == 'train':
            dataset = MelDataset(data_dir, split, num_max_data=kwargs['data_num'])
        else:
            dataset = MelDataset(data_dir, split)

    return DataLoader(dataset, batch_size=batch_size, num_workers=num_workers)


class MetricModel(nn.Module):
    def __init__(self, style_encoder='gst', idim=15626, n_dim=256, out_dim=64):
        super(MetricModel, self).__init__()


        self.style_encoder = StyleEncoder(idim=idim, style_layer=style_encoder)
        self.text_encoder = TextEncoder()

        # audio MLP
        self.audio_mlp = nn.Sequential(
            nn.Linear(n_dim, n_dim * 2),
            nn.BatchNorm1d(n_dim * 2),
            nn.ReLU(),
            nn.Dropout(),
            nn.Linear(n_dim * 2, out_dim)
        )

        # text MLP
        self.text_mlp = nn.Sequential(
            nn.Linear(512, n_dim * 2),
            nn.BatchNorm1d(n_dim * 2),
            nn.ReLU(),
            nn.Dropout(),
            nn.Linear(n_dim * 2, out_dim)
        )
        # tag MLP
        self.tag_mlp = nn.Sequential(
            nn.Linear(300, n_dim * 2),
            nn.BatchNorm1d(n_dim * 2),
            nn.ReLU(),
            nn.Dropout(),
            nn.Linear(n_dim * 2, out_dim)
        )

        self.loss_func = nn.TripletMarginWithDistanceLoss(distance_function=nn.CosineSimilarity())

    def forward(self, batch):
        # text_tag, audio_tag, spec, text, neg_spec, neg_text
        spec = self.style_encoder(batch['mel'])
        text = self.text_encoder(batch['text']['input_ids'], batch['text']['attention_mask'])

        text_tag_emb = self.tag_mlp(batch['text_label'])
        audio_tag_emb = self.tag_mlp(batch['mel_label'])
        audio_emb = self.audio_mlp(spec)
        text_emb = self.text_mlp(text)

        neg_spec = self.style_encoder(batch['neg_mel'])
        neg_text = self.text_encoder(batch['neg_text']['input_ids'], batch['neg_text']['attention_mask'])

        neg_spec_emb = self.audio_mlp(neg_spec)
        neg_text_emb = self.text_mlp(neg_text)

        loss = self.loss_func(text_tag_emb, text_emb, neg_text_emb)
        loss += self.loss_func(audio_tag_emb, audio_emb, neg_spec_emb)
        loss += self.loss_func(audio_emb, text_emb, neg_text_emb)

        return loss.mean()

    def audio_to_embedding(self, batch):
        emb = self.style_encoder(batch['mel'])
        emb = self.audio_mlp(emb)
        return emb

    def text_to_embedding(self, batch):
        pos = self.text_encoder(batch['text']['input_ids'], batch['text']['attention_mask'])
        pos = self.text_mlp(pos)
        neg = self.text_encoder(batch['neg_text']['input_ids'], batch['neg_text']['attention_mask'])
        neg = self.text_mlp(neg)
        return pos, neg

    def text_to_embedding_only(self, input_ids, attention_mask):
        embeds = self.text_encoder(input_ids, attention_mask)
        embeds = self.text_mlp(embeds)
        return embeds

    def evaluate(self, batch):
        audio_embed = self.audio_to_embedding(batch)
        text_positive_embed, text_negative_embed = self.text_to_embedding(batch)

        loss = self.loss_func(audio_embed, text_positive_embed, text_negative_embed)
        marginloss_func = nn.TripletMarginLoss()
        triplet_loss = marginloss_func(audio_embed, text_positive_embed, text_negative_embed)

        audio_embed = audio_embed.cpu().numpy()
        text_positive_embed = text_positive_embed.cpu().numpy()

        cosine_similarity = paired_cosine_distances(audio_embed, text_positive_embed)
        manhattan_distances = paired_manhattan_distances(audio_embed, text_positive_embed)
        euclidean_distances = paired_euclidean_distances(audio_embed, text_positive_embed)

        score = {
            "loss": loss.mean(),
            "triplet_loss": triplet_loss.mean(),
            "triplet_distance_loss": loss.mean(),
            "cosine_similarity": cosine_similarity.mean(),
            "manhattan_distance": manhattan_distances.mean(),
            "euclidean_distance": euclidean_distances.mean(),
        }
        return score
